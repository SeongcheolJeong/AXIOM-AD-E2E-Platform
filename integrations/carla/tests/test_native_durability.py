"""Native boundary tests over authored fake worlds, never simulator acceptance."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from ade2e.adapters import AdapterExecutionError
from ade2e.simulation_evidence import validate_simulation_artifacts
from axiom_carla import durability, native_journal
from axiom_carla.runner import CarlaRunner
from axiom_carla.version import ADAPTER_VERSION
from test_live_stack import install_live_world, live_request
from test_runner_evidence import fake_world_runner


class Sink:
    def __init__(self, fail_event=None, fail_phase=None):
        self.events, self.checkpoints = [], []
        self.fail_event, self.fail_phase = fail_event, fail_phase

    def event(self, kind, data, content=None):
        # Round-trip strict JSON models an actual serializer, not object aliases.
        value = json.loads(json.dumps(data, allow_nan=False))
        if kind == self.fail_event:
            raise OSError('Injected durable commit failure: ' + kind)
        self.events.append((kind, value, content))

    def checkpoint(self, payloads, phase):
        if phase == self.fail_phase:
            raise OSError('Injected checkpoint commit failure: ' + phase)
        saved = [p.model_copy(deep=True) for p in payloads]
        self.checkpoints.append((phase, saved))

    def role_name(self, role):
        return 'execution-unique-' + role


def metadata(world, monkeypatch):
    world.id = 18446744073709551614
    class Actors(list):
        def filter(self, _):
            return []
    monkeypatch.setattr(world, 'get_actors', lambda: Actors())
    original = world.spawn_actor
    def spawn(*args, **kwargs):
        actor = original(*args, **kwargs)
        if not hasattr(actor, 'id'):
            actor.id = 100 + (1 if getattr(world, 'lidar', None) is actor else 0)
        return actor
    monkeypatch.setattr(world, 'spawn_actor', spawn)
    return world


def json_artifact(payloads, name):
    return json.loads(next(p.content for p in payloads if p.name == name))


def test_live_checkpoints_are_valid_frozen_partial_and_preserve_real_ack_order(monkeypatch, carla_scenario):
    world = metadata(install_live_world(monkeypatch), monkeypatch)
    request, sink = live_request(carla_scenario), Sink()
    with durability.recording(sink):
        result = CarlaRunner().execute_with_context(request)
    assert len(result.trajectory) == 81 and len(world.vehicles[0].commands) == 80
    assert sink.events[0][0] == 'world_original'
    assert sink.events[0][1]['world_id'] == '18446744073709551614'
    assert sink.events[1][0] == 'world_mutation_intent'
    spawns = [data for kind, data, _ in sink.events if kind == 'spawn_returned']
    assert len(spawns) == 4 and len({data['role_name'] for data in spawns}) == 4
    assert all(data['role_name'].startswith('execution-unique-') for data in spawns)
    assert len([1 for kind, *_ in sink.events if kind == 'startup_sample']) == 8
    assert len([1 for kind, *_ in sink.events if kind == 'control_acknowledged']) == 80
    assert len([1 for kind, *_ in sink.events if kind == 'live_lidar_raw']) == 81
    for kind, data, raw in sink.events:
        if kind == 'live_lidar_raw':
            assert data['sha256'] == hashlib.sha256(raw).hexdigest() and data['size_bytes'] == len(raw)
    for phase, payloads in sink.checkpoints:
        validate_simulation_artifacts(request, payloads, completed=False, adapter_version=ADAPTER_VERSION)
        trace = json_artifact(payloads, 'carla-stack-trace.json')
        assert trace['status'] == 'partial' and all(t['status'] != 'running' for t in trace['ticks'])
    before = next(payloads for phase,payloads in sink.checkpoints if phase == 'before_ego_control_send')
    ack = next(payloads for phase,payloads in sink.checkpoints if phase == 'ego_control_acknowledged')
    after = next(payloads for phase,payloads in sink.checkpoints if phase == 'next_physics_before_optional_rpc')
    assert json_artifact(before, 'carla-stack-trace.json')['ticks'][0]['application_status'] == 'send_outcome_unknown'
    tick = json_artifact(ack, 'carla-stack-trace.json')['ticks'][0]
    assert tick['control_acknowledged'] and tick['next_world_frame'] is None
    tick = json_artifact(after, 'carla-stack-trace.json')['ticks'][0]
    assert tick['next_world_frame'] == tick['source_world_frame'] + 1 and tick['observed_control'] is None
    assert json_artifact(result.artifact_payloads, 'carla-stack-trace.json')['status'] == 'completed'
    assert sink.events[-1][0] == 'cleanup_finished' and sink.events[-1][1] == {'errors': [], 'world_verified': False}


@pytest.mark.parametrize('fail_phase,commands', [('before_ego_control_send', 0), ('ego_control_acknowledged', 1), ('next_physics_before_optional_rpc', 1)])
def test_checkpoint_failure_stops_next_actuation_and_preserves_cleanup(monkeypatch, carla_scenario, fail_phase, commands):
    world = metadata(install_live_world(monkeypatch), monkeypatch)
    sink = Sink(fail_phase=fail_phase)
    with durability.recording(sink), pytest.raises(AdapterExecutionError, match='checkpoint commit failure'):
        CarlaRunner().execute_with_context(live_request(carla_scenario))
    assert len(world.vehicles[0].commands) == commands
    assert all(actor.destroy_called for actor in world.vehicles)
    assert world.settings.synchronous_mode is False
    assert sink.events[-1][0] == 'cleanup_finished'


def test_failure_before_first_mutation_does_not_restore_or_touch_world(monkeypatch, carla_scenario):
    world = metadata(install_live_world(monkeypatch), monkeypatch)
    sink = Sink(fail_phase='before_world_mutation')
    with durability.recording(sink), pytest.raises(AdapterExecutionError):
        CarlaRunner().execute_with_context(live_request(carla_scenario))
    assert world.mutations == 0 and not world.vehicles
    assert not any(kind.startswith('cleanup') or kind == 'world_mutation_intent' for kind,*_ in sink.events)


def test_legacy_durable_prefix_retains_full_denominator_and_no_fake_ack(fake_world_runner, monkeypatch, carla_scenario):
    world = metadata(fake_world_runner(), monkeypatch)
    sink = Sink()
    with durability.recording(sink):
        result = CarlaRunner().execute(carla_scenario)
    phase, prefix = sink.checkpoints[-1]
    data = json_artifact(prefix, 'carla-legacy-prefix.json')
    assert data['status'] == 'partial' and len(data['trajectory']) == len(result.trajectory) == data['expected_observations'] == 81
    assert len(data['commands']) == 2 * data['expected_intervals'] == 160
    assert all(not command['acknowledged'] and command['status'] == 'api_returned_unacknowledged' for command in data['commands'])
    assert data['capture_plan_indices'] == [] and data['capture_enabled'] is False
    assert len([1 for kind,*_ in sink.events if kind == 'physics_snapshot']) == 82
    assert sink.events[-1][1]['world_verified'] is False


def test_cleanup_journal_failure_still_attempts_owned_cleanup():
    called = []
    sink = Sink(fail_event='cleanup_intent')
    with durability.recording(sink), pytest.raises(OSError, match='commit failure'):
        native_journal.cleanup('actor.destroy', lambda: called.append(True) or True, {'actor_id': 10}, acknowledgement=True)
    assert called == [True]
    assert sink.events == [('cleanup_returned', {'operation': 'actor.destroy', 'actor_id': 10, 'return_value': True,
        'acknowledgement': True, 'world_verified': False}, None)]


def test_collision_callback_thread_commits_only_on_owner_and_is_not_complete():
    import threading
    sink = Sink()
    with durability.recording(sink):
        journal = native_journal.CollisionJournal()
        thread = threading.Thread(target=lambda: journal.receive(SimpleNamespace(frame=7, timestamp=.35, other_actor=SimpleNamespace(id=22))))
        thread.start(); thread.join()
        assert not sink.events
        journal.flush()
    assert sink.events[0] == ('collision_observed', {'world_frame': 7, 'timestamp_s': .35, 'other_actor_id': 22, 'stream_complete': False}, None)


def test_successful_spawn_is_owned_for_cleanup_when_return_commit_fails(monkeypatch, carla_scenario):
    world = metadata(install_live_world(monkeypatch), monkeypatch)
    sink = Sink(fail_event='spawn_returned')
    with durability.recording(sink), pytest.raises(AdapterExecutionError, match='spawn_returned'):
        CarlaRunner().execute_with_context(live_request(carla_scenario))
    assert len(world.vehicles) == 1 and world.vehicles[0].destroy_called
    assert world.settings.synchronous_mode is False
    assert any(kind == 'spawn_intent' for kind,*_ in sink.events)
    assert not any(kind == 'spawn_returned' for kind,*_ in sink.events)


def test_mid_startup_checkpoint_contains_actual_partial_trace(monkeypatch, carla_scenario):
    world = metadata(install_live_world(monkeypatch), monkeypatch)
    class ThirdSampleFailure(Sink):
        def event(self, kind, data, content=None):
            if kind == 'startup_sample' and data['sample']['tick'] == 4:
                raise OSError('Injected fourth-sample journal failure')
            super().event(kind, data, content)
    sink = ThirdSampleFailure()
    with durability.recording(sink), pytest.raises(AdapterExecutionError, match='fourth-sample'):
        CarlaRunner().execute_with_context(live_request(carla_scenario))
    phase, payloads = sink.checkpoints[-1]
    report = json_artifact(payloads, 'carla-startup.json')
    assert phase == 'startup_sample' and report['completed_ticks'] == len(report['trace']) == 3
    assert report['status'] == 'failed' and report['reason_code'] == 'interrupted_checkpoint'
    assert not world.vehicles[0].commands


def test_legacy_selected_source_prefix_contains_exact_bytes_and_mcap(monkeypatch, carla_scenario):
    import io
    from mcap.reader import make_reader
    from test_capture_recording import install_capture_world
    world = metadata(install_capture_world(monkeypatch), monkeypatch)
    world.camera.id = 102
    sink = Sink()
    with durability.recording(sink):
        result = CarlaRunner().execute(carla_scenario)
    raw = [(kind,data,content) for kind,data,content in sink.events if kind in {'legacy_rgb_raw','legacy_lidar_raw'}]
    assert len(raw) == 6
    assert [kind for kind,*_ in raw] == ['legacy_rgb_raw', 'legacy_lidar_raw'] * 3
    selected = [payloads for phase,payloads in sink.checkpoints if phase == 'legacy_selected_capture_retained']
    assert len(selected) == 3
    for index, payloads in enumerate(selected, 1):
        recording = next(p for p in payloads if p.kind == 'sensor_recording')
        messages = list(make_reader(io.BytesIO(recording.content)).iter_messages())
        assert len(messages) == 2*index
        prefix = json_artifact(payloads, 'carla-legacy-prefix.json')
        assert prefix['expected_observations'] == 81 and prefix['capture_plan_indices'] == [0,40,80]
    assert len(result.trajectory) == 81


def test_raw_lidar_commit_failure_occurs_before_first_kernel_control(monkeypatch, carla_scenario):
    world = metadata(install_live_world(monkeypatch), monkeypatch)
    sink = Sink(fail_event='live_lidar_raw')
    with durability.recording(sink), pytest.raises(AdapterExecutionError, match='live_lidar_raw'):
        CarlaRunner().execute_with_context(live_request(carla_scenario))
    assert not world.vehicles[0].commands
    physics = [data for kind,data,_ in sink.events if kind == 'physics_snapshot' and data['phase'] == 'measured_before_optional_rpc']
    assert len(physics) == 1
    assert sink.checkpoints[-1][0] == 'initial_physics_before_optional_rpc'


def test_native_process_death_after_durable_ack_recovers_exact_partial(tmp_path, carla_scenario):
    """Real OS exit/SQLite recovery; native world and sensor bytes are authored."""
    import subprocess
    import sys
    from pathlib import Path
    from ade2e.execution_attempt import ExecutionAttemptRequest
    from axiom_carla.remote_durable import decode_archive
    from axiom_carla.spool import Store
    request = live_request(carla_scenario)
    attempt_request = ExecutionAttemptRequest(execution_id=request.execution_id, adapter_id='carla',
        adapter_version=ADAPTER_VERSION, scenario=request.scenario, context=request.context, input_hash=request.input_hash)
    request_file = tmp_path / 'request.json'
    request_file.write_text(attempt_request.model_dump_json())
    program = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
import pytest
from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.simulation_context import SimulationExecutionRequest
from axiom_carla import durability
from axiom_carla.runner import CarlaRunner
from axiom_carla.spool import Store
from test_native_durability import metadata
from test_live_stack import install_live_world
request = ExecutionAttemptRequest.model_validate_json(Path(sys.argv[2]).read_bytes())
with pytest.MonkeyPatch.context() as patch:
    metadata(install_live_world(patch), patch)
    with Store(sys.argv[1]).begin(request) as attempt:
        class Sink:
            event = attempt.event
            role_name = attempt.role_name
            def checkpoint(self, payloads, phase):
                attempt.checkpoint(payloads, phase)
                if phase == 'ego_control_acknowledged':
                    os._exit(79)
        with durability.recording(Sink()):
            CarlaRunner().execute_with_context(SimulationExecutionRequest(execution_id=request.execution_id,
                input_hash=request.input_hash, scenario=request.scenario, context=request.context))
'''
    process = subprocess.run([sys.executable, '-c', program, str(tmp_path/'store'), str(request_file),
        str(Path(__file__).resolve().parent)], capture_output=True, timeout=30)
    assert process.returncode == 79, process.stderr.decode()
    store = Store(tmp_path/'store')
    status = store.inspect(attempt_request)
    assert status.state == 'interrupted' and status.mutations_started and status.cleanup_status == 'unknown'
    attempt = store.find(request.execution_id)
    archive = decode_archive(attempt_request, status, attempt.archive())
    payloads = archive.failure.artifact_payloads
    validate_simulation_artifacts(request, payloads, completed=False, adapter_version=ADAPTER_VERSION)
    trace = json_artifact(payloads, 'carla-stack-trace.json')
    assert trace['status'] == 'partial' and len(trace['ticks']) == 80
    tick = trace['ticks'][0]
    assert tick['status'] == 'failed' and tick['control_acknowledged'] is True and tick['next_world_frame'] is None
    assert len(json_artifact(payloads, 'carla-stack-physics.json')['observations']) == 1
    journal = attempt.journal()
    assert len([event for event in journal['entries'] if event['kind'] == 'control_acknowledged']) == 1
    assert not any(event['kind'].startswith('cleanup_') for event in journal['entries'])
    assert store.inspect(attempt_request).archive_sha256 == status.archive_sha256


def test_legacy_cleanup_failure_preserves_prefix_and_selected_capture(monkeypatch, carla_scenario):
    from test_capture_recording import install_capture_world
    world = metadata(install_capture_world(monkeypatch), monkeypatch)
    world.camera.id = 102
    world.destroy_ok = False
    sink = Sink()
    with durability.recording(sink), pytest.raises(AdapterExecutionError, match='cleanup') as caught:
        CarlaRunner().execute(carla_scenario)
    payloads = caught.value.artifact_payloads
    prefix = json_artifact(payloads, 'carla-legacy-prefix.json')
    assert prefix['phase'] == 'cleanup_failed' and len(prefix['trajectory']) == 81
    assert sum(p.kind == 'sensor_recording' for p in payloads) == 1
    assert sum(p.kind == 'camera_rgb' for p in payloads) == 3
    assert len({p.name for p in payloads}) == len(payloads)
    assert sink.events[-1][0] == 'cleanup_finished' and sink.events[-1][1]['errors']
