"""Mocked moving-rig adapter checks. These are not native CARLA acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import zipfile
from unittest.mock import patch
import pytest
from ade2e.driving_sensor_capture_schemas import DrivingSensorCaptureSpec, driving_capture_template
from ade2e.experiment_schemas import ExperimentCreate
from axiom_carla import driving_multisensor_native as native
from axiom_carla.driving_multisensor import DrivingMultisensorHandler, validate_driving_bundle
from test_driving_multisensor_native import capture_fixture, specification, World


def bundle(folder, manifest):
    (folder / 'capture-manifest.json').write_bytes(native.canonical(manifest))
    archive = folder / 'unit.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as target:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.name != 'unit.zip':
                target.write(path, str(path.relative_to(folder)))
    return archive, {'status': 'completed', 'external_execution_id': folder.name, 'manifest': 'capture-manifest.json',
        'manifest_sha256': native.sha_file(folder / 'capture-manifest.json'), 'frame_count': manifest['frame_count'],
        'archive_sha256': native.sha_file(archive), 'archive_size_bytes': archive.stat().st_size}


@pytest.fixture
def retained(tmp_path):
    return capture_fixture(tmp_path, spec=specification(cameras=False))


def test_moving_bundle_accepts_changing_pose_and_complete_original_frames(retained):
    spec, probe, manifest, folder, _ = retained
    archive, receipt = bundle(folder, manifest)
    result, raw = validate_driving_bundle(archive, receipt, spec, folder.name, probe)
    assert result == manifest and raw == (folder / 'capture-manifest.json').read_bytes()
    assert manifest['records'][0]['ego_world_transform'] != manifest['records'][-1]['ego_world_transform']


@pytest.mark.parametrize('mutation', [
    lambda m: m['records'][0].update(frame=m['records'][0]['frame'] - 1),
    lambda m: m['records'][0].update(ego_world_transform=m['ego_initial_transform']),
    lambda m: m['motion']['ticks'][1]['requested_control'].update(throttle=0.),
    lambda m: m['motion']['ticks'][0].update(actuator_acknowledged=False),
    lambda m: m['motion']['ticks'][0].update(sensor_keys=['lidar']),
    lambda m: m.update(distance_travelled_m=0.),
    lambda m: m['engine_interlock'].update(reservation_cleared_after_confirmed_cleanup=False),
    lambda m: m['cleanup_observation'].update(owned_snapshot_absent_actor_ids=[]),
    lambda m: m['sensors'][0]['native_readback'].update(range='99'),
    lambda m: m.update(warmup_policy='silently_reused_first_moving_frame'),
])
def test_moving_corruption_is_rejected(retained, mutation):
    spec, probe, manifest, folder, _ = retained
    mutation(manifest)
    archive, receipt = bundle(folder, manifest)
    with pytest.raises(ValueError):
        validate_driving_bundle(archive, receipt, spec, folder.name, probe)


def test_original_file_bytes_are_verified(retained):
    spec, probe, manifest, folder, _ = retained
    (folder / manifest['artifacts'][0]['name']).write_bytes(b'corrupt')
    archive, receipt = bundle(folder, manifest)
    with pytest.raises(ValueError, match='size mismatch'):
        validate_driving_bundle(archive, receipt, spec, folder.name, probe)


def handler():
    value = DrivingMultisensorHandler()
    value.target = 'operator@native'; value.python = '/python'; value.script = '/driving.py'
    value.directory = '/captures'; value.engine_spool = '/shared-spool'
    return value


def test_prepare_snapshot_matches_native_schema_and_pins_all_sources():
    capture = handler(); world = World()
    spec = ExperimentCreate(name='Mock moving contract', kind='sensor_capture', adapter_id='carla-driving-multisensor', input=driving_capture_template())
    with patch.object(capture, '_probe', side_effect=lambda rig: native.probe(world.client(), rig)):
        snapshot = capture.prepare(spec)
    assert snapshot['schema_version'] == 'carla-driving-multisensor-experiment/1'
    assert set(snapshot['rigs']) == {'single'} and set(snapshot['native_capability_snapshots']) == {'single'}
    assert snapshot['rigs']['single']['seed'] == 42
    assert len(snapshot['rigs']['single']['sensors']) == 4
    assert snapshot['native_feedback_script_sha256'] == native.sha_file(native.feedback.__file__)
    assert capture.metadata().resource_id == 'carla'


def test_prepare_ab_rejects_changed_controller_or_identical_rigs_before_probe():
    a = driving_capture_template(); b = copy.deepcopy(a)
    capture = handler()
    def request():
        return ExperimentCreate(name='Mock A/B', kind='sensor_capture', adapter_id='carla-driving-multisensor',
            comparison_kind='same_scene_sensor_ab', input={'rig_a': a, 'rig_b': b})
    with patch.object(capture, '_probe') as probe:
        with pytest.raises(ValueError, match='configuration change'): capture.prepare(request())
        b['controller']['target_speed_mps'] = 4
        with pytest.raises(ValueError, match='fix scene'): capture.prepare(request())
        probe.assert_not_called()


def test_prepare_ab_freezes_both_native_rigs_without_dispatch():
    a = driving_capture_template(); b = copy.deepcopy(a)
    b['rig_revision'] = 2; b['sensors'][0]['parameters']['fov'] = '70'
    capture = handler(); world = World()
    spec = ExperimentCreate(name='Mock A/B', kind='sensor_capture', adapter_id='carla-driving-multisensor',
        comparison_kind='same_scene_sensor_ab', input={'rig_a': a, 'rig_b': b})
    with patch.object(capture, '_probe', side_effect=lambda rig: native.probe(world.client(), rig)), patch('subprocess.Popen') as dispatch:
        snapshot = capture.prepare(spec)
        dispatch.assert_not_called()
    assert len(snapshot['rigs']) == 2
    assert snapshot['rigs']['rig_b']['sensors'][0]['native_parameters']['fov'] == '70'
    assert snapshot['workload']['capture_count'] == 2


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(native_script_sha256='0' * 64),
    lambda p: p.update(native_feedback_script_sha256='0' * 64),
    lambda p: p.update(active_dynamic_actor_ids=[7]),
    lambda p: p.update(server_version='wrong'),
])
def test_prepare_rejects_unowned_or_changed_native_source(mutation):
    capture = handler(); world = World()
    spec = ExperimentCreate(name='Mock', kind='sensor_capture', adapter_id='carla-driving-multisensor', input=driving_capture_template())
    def probe(rig):
        result = native.probe(world.client(), rig); mutation(result); return result
    with patch.object(capture, '_probe', side_effect=probe), pytest.raises(ValueError): capture.prepare(spec)


def test_changed_source_after_preview_never_dispatches(tmp_path):
    capture = handler(); world = World()
    spec = ExperimentCreate(name='Mock', kind='sensor_capture', adapter_id='carla-driving-multisensor', input=driving_capture_template())
    with patch.object(capture, '_probe', side_effect=lambda rig: native.probe(world.client(), rig)):
        snapshot = capture.prepare(spec)
    snapshot['native_feedback_script_sha256'] = '0' * 64
    with patch('subprocess.Popen') as dispatch, pytest.raises(ValueError, match='changed after preview'):
        capture.execute(snapshot, tmp_path, lambda: False)
    dispatch.assert_not_called()


@pytest.mark.parametrize('duration', [20, 30])
def test_new_public_profile_keeps_native_limits(duration):
    value = driving_capture_template(); value['duration_seconds'] = duration
    result = DrivingSensorCaptureSpec.model_validate(value)
    assert len(result.sensors) == 4 and result.duration_seconds == duration
    value['controller']['route_length_m'] = 30
    with pytest.raises(ValueError): DrivingSensorCaptureSpec.model_validate(value)
