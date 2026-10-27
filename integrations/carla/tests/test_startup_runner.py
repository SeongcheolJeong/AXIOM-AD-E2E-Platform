"""Lifecycle and evidence integration with scripted worlds, not engine validation."""
import hashlib
import json
import struct
from types import SimpleNamespace as NS

import pytest

from ade2e.adapters import AdapterExecutionError
from axiom_carla.runner import CarlaRunner, _copy_transform
from test_capture_recording import install_capture_world
from test_runner_evidence import fake_world_runner, native_transform, transform


def startup_evidence(payloads):
    artifact = next(item for item in payloads if item.kind == 'startup_manifest')
    return artifact, json.loads(artifact.content)


def instrument_actors(world, monkeypatch, unstable_until):
    calls = []
    original = world.try_spawn_actor
    def spawn(blueprint, pose):
        actor = original(blueprint, pose)
        actor.get_acceleration = lambda: NS(x=0, y=0, z=1 if world.frame < unstable_until else 0)
        set_velocity = actor.set_target_velocity
        def target(value):
            calls.append((actor.id, world.frame))
            set_velocity(value)
        actor.set_target_velocity = target
        return actor
    monkeypatch.setattr(world, 'try_spawn_actor', spawn)
    return calls


def test_explicit_native_transform_copy_does_not_mutate_or_alias_map_pose():
    carla = NS(Transform=native_transform, Location=lambda **kwargs: NS(**kwargs), Rotation=lambda **kwargs: NS(**kwargs))
    source = transform(1, 2, .6)
    source.rotation.pitch, source.rotation.yaw, source.rotation.roll = 2, 3, 4
    target = _copy_transform(carla, source, .3)
    assert vars(source.location) == {'x': 1, 'y': 2, 'z': .6}
    assert target.location.z == pytest.approx(.9)
    assert vars(target.rotation) == {'pitch': 2, 'yaw': 3, 'roll': 4}
    target.location.x = -5
    target.rotation.pitch = 45
    assert source.location.x == 1 and source.rotation.pitch == 2


def test_success_retains_raw_startup_and_hash_link_before_motion(fake_world_runner, carla_scenario, monkeypatch):
    world = fake_world_runner()
    calls = instrument_actors(world, monkeypatch, unstable_until=20)
    raw_ego, raw_lead = transform(z=.6), transform(x=45)
    monkeypatch.setattr(CarlaRunner, '_straight_spawn', lambda *_: (raw_ego, raw_lead))
    result = CarlaRunner().execute(carla_scenario)
    artifact, report = startup_evidence(result.artifact_payloads)
    assert report['status'] == 'passed' and report['completed_ticks'] == 24
    assert calls == [(1, 24), (2, 24)]
    assert len(report['trace']) == 24 and report['stable_streak'] == 5
    assert report['context']['stage'] == 'rest_before_initial_target_velocity'
    assert report['context']['scenario_revision'] == carla_scenario.revision
    assert report['context']['server_version'] == 'unit-test-only'
    assert report['context']['spawn_transforms']['axiom_ego']['source']['position_m']['z'] == .6
    assert raw_ego.location.z == .6 and raw_lead.location.z == 0
    manifest = json.loads(next(item.content for item in result.artifact_payloads if item.kind == 'sensor_manifest'))
    assert manifest['startup']['sha256'] == hashlib.sha256(artifact.content).hexdigest()
    assert manifest['startup']['size_bytes'] == len(artifact.content)
    assert len(result.metrics) == 5 and len(result.trajectory) == 81
    assert [result.trajectory[0].time_s, result.trajectory[-1].time_s] == [0, 4]
    assert world.frame == 24+81  # No hidden trimmed measured ticks.
    assert all(actor.destroy_called for actor in world.vehicles)


def test_failed_gate_never_imposes_speed_or_enters_measurement(fake_world_runner, carla_scenario, monkeypatch):
    world = fake_world_runner()
    calls = instrument_actors(world, monkeypatch, unstable_until=1000)
    def forbidden(*args):
        pytest.fail('No measured controller call is allowed after a failed gate')
    monkeypatch.setattr(CarlaRunner, '_control', forbidden)
    with pytest.raises(AdapterExecutionError, match='startup stability gate failed') as info:
        CarlaRunner().execute(carla_scenario)
    _, report = startup_evidence(info.value.artifact_payloads)
    assert calls == [] and world.frame == 100
    assert report['status'] == 'failed' and report['reason_code'] == 'stability_timeout'
    assert len(report['trace']) == 100
    assert all(actor.destroy_called and not actor.is_alive for actor in world.vehicles)
    assert world.collision.stopped and world.collision.destroyed
    assert world.settings.synchronous_mode is False and world.weather == 'original-weather'
    assert {item.kind for item in info.value.artifact_payloads} == {'startup_manifest'}


def test_cleanup_failure_preserves_failed_startup_and_reports_both_failures(fake_world_runner, carla_scenario, monkeypatch):
    world = fake_world_runner(destroy_ok=False)
    instrument_actors(world, monkeypatch, unstable_until=1000)
    original = world.set_weather
    def fail_restore(weather):
        if weather == 'original-weather':
            raise ValueError('scripted weather restoration failure')
        original(weather)
    monkeypatch.setattr(world, 'set_weather', fail_restore)
    with pytest.raises(AdapterExecutionError, match='cleanup/settings restore failed') as info:
        CarlaRunner().execute(carla_scenario)
    _, report = startup_evidence(info.value.artifact_payloads)
    cleanup = json.loads(next(item.content for item in info.value.artifact_payloads if item.kind == 'execution_diagnostic'))
    assert report['reason_code'] == 'stability_timeout'
    assert 'startup stability gate failed' in cleanup['preceding_error']['message']
    assert 'scripted weather restoration failure' in cleanup['errors']
    assert cleanup['errors'].count('Vehicle destruction was not acknowledged') == 2
    assert world.settings.synchronous_mode is False


@pytest.mark.parametrize('payload', [struct.pack('<ffff', 1, float('nan'), 2, .5), b'broken-stride'])
def test_capture_encoding_failure_preserves_startup_and_cleans_up(monkeypatch, carla_scenario, payload):
    world = install_capture_world(monkeypatch)
    world.raw_cloud = payload
    with pytest.raises(AdapterExecutionError, match='execution was attempted') as info:
        CarlaRunner().execute(carla_scenario)
    _, report = startup_evidence(info.value.artifact_payloads)
    assert report['status'] == 'passed' and report['completed_ticks'] == 8
    assert world.camera.stopped and world.lidar.destroyed
    assert all(actor.destroy_called for actor in world.vehicles)
    assert world.settings.synchronous_mode is False


def test_recording_assembly_failure_after_teardown_preserves_startup_and_captures(monkeypatch, carla_scenario):
    world = install_capture_world(monkeypatch)
    def fail(*args, **kwargs):
        assert world.camera.destroyed and world.lidar.destroyed
        assert all(actor.destroy_called for actor in world.vehicles)
        raise ValueError('scripted MCAP writer failure')
    monkeypatch.setattr('axiom_carla.runner.capture_recording', fail)
    with pytest.raises(AdapterExecutionError, match='result assembly.*scripted MCAP writer failure') as info:
        CarlaRunner().execute(carla_scenario)
    _, report = startup_evidence(info.value.artifact_payloads)
    assert report['status'] == 'passed'
    assert sum(item.kind == 'camera_rgb' for item in info.value.artifact_payloads) == 3
    assert sum(item.kind == 'lidar' for item in info.value.artifact_payloads) == 3
    assert not any(item.kind == 'sensor_recording' for item in info.value.artifact_payloads)
    assert world.settings.synchronous_mode is False


def test_final_result_contract_failure_preserves_diagnostics(fake_world_runner, monkeypatch, carla_scenario):
    world = fake_world_runner()
    def fail(**kwargs):
        assert all(actor.destroy_called for actor in world.vehicles)
        raise ValueError('scripted final result validation failure')
    monkeypatch.setattr('axiom_carla.runner.SimulationResult', fail)
    with pytest.raises(AdapterExecutionError, match='result assembly.*final result validation') as info:
        CarlaRunner().execute(carla_scenario)
    assert startup_evidence(info.value.artifact_payloads)[1]['status'] == 'passed'


def test_process_interrupt_remains_baseexception_and_cleanup_still_runs(fake_world_runner, monkeypatch, carla_scenario):
    world = fake_world_runner()
    def interrupt(*args):
        raise KeyboardInterrupt('scripted process interrupt')
    monkeypatch.setattr(CarlaRunner, '_control', interrupt)
    with pytest.raises(KeyboardInterrupt):
        CarlaRunner().execute(carla_scenario)
    assert all(actor.destroy_called for actor in world.vehicles)
    assert world.settings.synchronous_mode is False
