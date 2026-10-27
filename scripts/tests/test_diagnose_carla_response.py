"""Diagnostic protocol over a fake world: these tests are not CARLA physics evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'integrations/carla/tests'))
from test_live_stack import install_live_world, live_request
from ade2e.schemas import Scenario
from axiom_carla.runner import CarlaRunner
import axiom_carla.version as version
import test_live_stack

spec = importlib.util.spec_from_file_location('response_diagnostic_script', ROOT/'scripts/diagnose_carla_response.py')
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


@pytest.fixture
def environment(monkeypatch, tmp_path):
    world = install_live_world(monkeypatch)
    carla, client, _, _ = CarlaRunner()._connect()
    client.set_timeout = lambda _: None
    client.get_client_version = lambda: 'authored-fake-world'
    monkeypatch.setattr(version, 'ADAPTER_VERSION', '0.4.0')
    monkeypatch.setattr(test_live_stack, 'ADAPTER_VERSION', '0.4.0')
    map_original = world.get_map
    def get_map():
        value = map_original()
        value.to_opendrive = lambda: 'authored-fake-map'
        return value
    world.get_map = get_map
    class ActorList(list):
        def filter(self, _):
            return []
    world.get_actors = lambda: ActorList()
    spawn = world.spawn_actor
    def spawn_actor(*args, **kwargs):
        sensor = spawn(*args, **kwargs)
        sensor.id = 100
        return sensor
    world.spawn_actor = spawn_actor
    original_spawn = world.try_spawn_actor
    world.physics_applied = []
    def spawn_vehicle(*args, **kwargs):
        actor = original_spawn(*args, **kwargs)
        original_physics = actor.apply_physics_control
        def apply(value):
            world.physics_applied.append((actor.id, diagnostic.physics(value)))
            return original_physics(value)
        actor.apply_physics_control = apply
        def telemetry():
            assert world.frame > 0, 'Telemetry must never be called before at least one physics tick'
            wheel = SimpleNamespace(**{key: float('nan') if key == 'normalized_long_force' else .1 for key in diagnostic.WHEEL_FIELDS})
            return SimpleNamespace(**{key: 1.0 for key in diagnostic.TELEMETRY_FIELDS}, wheels=[wheel]*4)
        actor.get_telemetry_data = telemetry
        return actor
    world.try_spawn_actor = spawn_vehicle
    def batch(commands, do_tick):
        assert do_tick is False and len(commands) == 2
        for actor_id, control in commands:
            world.vehicles[actor_id-1].apply_control(control)
        return [SimpleNamespace(actor_id=actor_id, has_error=lambda: False) for actor_id, _ in commands]
    client.apply_batch_sync = batch
    scenario = Scenario(id='diagnostic-test', name='Authored diagnostic protocol only', category='lane_follow', map_name='Town01',
        duration_s=4, seed=42, parameters={'ego_speed_mps': 2, 'target_speed_mps': 2, 'friction': .8},
        revision=1, created_at='2026-09-13T00:00:00Z', updated_at='2026-09-13T00:00:00Z')
    request = live_request(scenario)
    path = tmp_path/'request.json'
    path.write_text(request.model_dump_json())
    return world, client, path


@pytest.mark.parametrize('trial,applied,rest_min', [('baseline', 2, 8), ('rest40', 2, 40), ('native_physics', 0, 8), ('reapply_native', 2, 8)])
def test_single_trial_retains_exact_sources_full_denominator_and_physics(environment, tmp_path, trial, applied, rest_min):
    world, client, request = environment
    directory = tmp_path/trial
    report = diagnostic.diagnose(request, directory, trial=trial)
    assert report['status'] == 'completed', report.get('error', report['cleanup'])
    assert report['actual_commands'] == report['acknowledged_commands'] == 80
    rows = [row for row in report['observations'] if row['phase'] == 'measurement']
    assert len(rows) == 81 and [row['index'] for row in rows] == list(range(81))
    assert len(world.physics_applied) == applied
    assert report['measurement_start_frame'] >= rest_min+1
    for role in ('ego', 'lead'):
        entry = report['physics'][role]
        expected = .8 if trial in ('baseline', 'rest40') else 3.5
        assert entry['post_startup_readback']['values']['wheels'][0]['tire_friction'] == pytest.approx(expected)
        if trial in ('baseline', 'rest40'):
            assert entry['exact_differences'][0]['field'] == 'wheels.0.tire_friction'
        else:
            assert entry['exact_differences'] == []
    raw = (directory/'raw-lidar-xyzi.bin').read_bytes()
    for row, command in zip(rows, report['commands']):
        assert command['source_world_frame'] == row['world_frame']
        assert command['next_world_frame'] == row['world_frame']+1
        ref = row['raw_lidar']
        assert hashlib.sha256(raw[ref['offset_bytes']:ref['offset_bytes']+ref['size_bytes']]).hexdigest() == ref['sha256']
        t = row['rpc_readings']['actors']['ego']['telemetry']
        assert t['status'] == 'invalid_fields'
        assert t['values']['wheels'][0]['normalized_long_force'] is None
        assert any(v['raw'] == 'nan' for v in t['invalid_fields'])
    assert world.settings.synchronous_mode is False and world.lidar.destroyed
    assert all(actor.destroy_called for actor in world.vehicles)
    json.loads((directory/'report.json').read_bytes(), parse_constant=lambda x: pytest.fail(x))
    assert (directory/'report.sha256').read_text().startswith(hashlib.sha256((directory/'report.json').read_bytes()).hexdigest())


def test_send_failure_is_uncertain_preserves_first_observation_and_cleanup(environment, tmp_path):
    world, client, request = environment
    def fail(*_):
        raise TimeoutError('Injected uncertain transport')
    client.apply_batch_sync = fail
    report = diagnostic.diagnose(request, tmp_path/'failed')
    assert report['status'] == 'failed'
    assert report['actual_measurement_observations'] == 1
    assert report['actual_commands'] == 1 and report['acknowledged_commands'] == 0
    assert report['commands'][0]['application_status'] == 'send_outcome_unknown'
    assert all(actor.destroy_called for actor in world.vehicles)
    assert world.settings.synchronous_mode is False
    assert (tmp_path/'failed/sources.mcap').stat().st_size > 0


def test_refuses_overwrite_and_version_before_world_changes(environment, monkeypatch, tmp_path):
    world, _, request = environment
    monkeypatch.setattr(version, 'ADAPTER_VERSION', '0.4.1')
    destination = tmp_path/'wrong-version'
    report = diagnostic.diagnose(request, destination)
    assert report['status'] == 'failed' and not world.vehicles
    assert '0.4.0' in report['error']['message']
    saved = (destination/'report.json').read_bytes()
    with pytest.raises(FileExistsError):
        diagnostic.diagnose(request, destination)
    assert (destination/'report.json').read_bytes() == saved


def test_uncertain_state_is_durable_before_control_rpc(environment, tmp_path):
    _, client, request = environment
    directory = tmp_path/'durable-send'
    def inspect_then_fail(*_):
        saved = json.loads((directory/'report.json').read_bytes())
        assert saved['commands'][-1]['application_status'] == 'send_outcome_unknown'
        assert saved['actual_commands'] if 'actual_commands' in saved else len(saved['commands']) == 1
        raise TimeoutError('Simulated command response lost')
    client.apply_batch_sync = inspect_then_fail
    report = diagnostic.diagnose(request, directory)
    assert report['status'] == 'failed'
    assert report['commands'][0]['application_status'] == 'send_outcome_unknown'


def test_cleanup_waits_for_stale_actor_cache_without_tick_or_repeat_destroy(environment, tmp_path):
    world, _, request = environment
    waited = []
    class ActorList(list):
        def filter(self, _):
            return []
    def actors():
        destroyed = world.vehicles and all(a.destroy_called for a in world.vehicles)
        return ActorList(world.vehicles if destroyed and not waited else [])
    world.get_actors = actors
    def wait_for_tick(seconds):
        assert world.settings.synchronous_mode is False
        assert 0 < seconds <= 2
        waited.append(world.frame)
        return world.get_snapshot()
    world.wait_for_tick = wait_for_tick
    report = diagnostic.diagnose(request, tmp_path/'cached-cleanup')
    assert report['status'] == 'completed', report.get('error', report['cleanup'])
    assert len(waited) == 1
    checks = report['cleanup_actor_checks']
    assert len(checks) == 2 and checks[0]['own_actor_ids_remaining'] == [1, 2]
    assert checks[1]['own_actor_ids_remaining'] == []
    assert checks[0]['world_frame'] == checks[1]['world_frame'] == waited[0]
    assert report['actual_commands'] == 80


def test_configure_after_spawn_is_only_intervention_and_preserves_original_trial(environment, tmp_path):
    world, _, request = environment
    original_spawn = world.try_spawn_actor
    def reset_prior_tires_on_spawn(*args, **kwargs):
        for previous in world.vehicles:
            for wheel in previous.physics.wheels:
                wheel.tire_friction = 3.5
        return original_spawn(*args, **kwargs)
    world.try_spawn_actor = reset_prior_tires_on_spawn
    spec = importlib.util.spec_from_file_location('configured_response_diagnostic', ROOT/'scripts/diagnose_carla_response_configured.py')
    configured = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(configured)
    assert configured.TRIALS == ('configure_after_spawn',)
    report = configured.diagnose(request, tmp_path/'after-spawn')
    assert report['status'] == 'completed', report.get('error', report['cleanup'])
    assert report['schema_version'] == 'carla-response-diagnostic/2'
    assert report['actual_commands'] == 80 and report['actual_measurement_observations'] == 81
    assert len(world.physics_applied) == 2
    for role in ('ego', 'lead'):
        assert report['physics'][role]['post_startup_readback']['values']['wheels'][0]['tire_friction'] == pytest.approx(.8)
    assert hashlib.sha256((ROOT/'scripts/diagnose_carla_response.py').read_bytes()).hexdigest() == report['parent_script_sha256']
