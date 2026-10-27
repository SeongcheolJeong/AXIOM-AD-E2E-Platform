"""Native scene contract mocks; no simulator, map or sensor validation claim."""
import copy
import hashlib
import json
import shutil
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from ade2e.experiment_schemas import ExperimentCreate
from axiom_carla import feedback_native as native, sensor_scene as scene
from axiom_carla.feedback import FeedbackSensorHandler, validate_feedback_bundle
from test_feedback_sensor import World, specification, captured, bundle


def rewritten(folder, manifest, name, mutation):
    value = json.loads((folder/name).read_bytes())
    mutation(value)
    data = native.canonical(value)
    (folder/name).write_bytes(data)
    entry = next(e for e in manifest['artifacts'] if e['name'] == name)
    previous = entry['size_bytes']
    entry.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    manifest['native_bytes'] += len(data) - previous
    return bundle(folder, manifest)


def configured(monkeypatch):
    for key, value in {'AXIOM_CARLA_FEEDBACK_SSH': 'operator@example', 'AXIOM_CARLA_FEEDBACK_PYTHON': '/python',
                       'AXIOM_CARLA_FEEDBACK_SCRIPT': '/feedback.py', 'AXIOM_CARLA_FEEDBACK_DIR': '/results',
                       'AXIOM_CARLA_FEEDBACK_ENGINE_SPOOL': '/spool'}.items():
        monkeypatch.setenv(key, value)
    return FeedbackSensorHandler()


def test_native_scene_contains_original_map_dimensions_offset_and_exact_response_joins(tmp_path):
    spec, probe, manifest, folder, world = captured(tmp_path)
    archive, receipt = bundle(folder)
    _, _, trace, trace_raw = validate_feedback_bundle(archive, receipt, spec, folder.name, probe)
    document = json.loads((folder/'sensor-scene.json').read_bytes())
    scene.validate_scene(document, (folder/'map.xodr').read_bytes(), manifest, trace, trace_raw)
    assert document['ego']['native_bounding_box']['local_transform']['position_m'] == manifest['ego_bounding_box']['local_transform']['position_m']
    assert document['ego']['native_bounding_box']['dimensions_m'] == [4.2, 1.9, 1.6]
    assert document['ego']['native_bounding_box']['extent_m'] == [.0+2.1, .95, .8]
    assert document['source']['map_opendrive_sha256'] == hashlib.sha256((folder/'map.xodr').read_bytes()).hexdigest()
    assert document['source']['trace_sha256'] == hashlib.sha256(trace_raw).hexdigest()
    assert len(document['frames']) == 20
    for row, tick, record in zip(document['frames'], trace['ticks'], manifest['records']):
        assert row['frame'] == tick['input_frame'] + 1 == record['frame']
        assert row['ego_world_transform'] == tick['ego']['world_transform']
        assert row['cameras'][0]['sensor_world_transform'] == record['sensor_world_transform']
    assert not world.actors and manifest['cleanup_observation']['controlled_cleanup_tick_count'] == 1


def test_sampling_sorts_native_s_and_splits_discontinuities_without_fake_lane_connection():
    world = World(); native_map = world.get_map()
    # Three points then an eight-metre gap; unsorted native enumeration.
    samples = [world.waypoint(x) for x in (14, 2, 0, 12, 4)]
    samples[0].lane_width = 4.1; samples[3].lane_width = 3.8
    native_map.generate_waypoints = lambda spacing: samples
    route = native.route_for(world, specification())
    document, raw = scene.sampled_map(native_map, route, route['map_opendrive_sha256'])
    assert [[p['s_m'] for p in s['points']] for s in document['lane_segments']] == [[0., 2., 4.], [12., 14.]]
    assert document['lane_segments'][1]['points'][0]['lane_width_m'] == 3.8
    assert document['lane_segments'][1]['points'][1]['lane_width_m'] == 4.1
    assert document['sample_count'] == 5 and document['segment_count'] == 2
    assert raw == native_map.to_opendrive().encode()


def test_sampling_rejects_unknown_source_lane_and_conflicting_duplicate_points():
    world = World(); native_map = world.get_map(); route = native.route_for(world, specification())
    wrong = world.waypoint(2); wrong.lane_id = -2
    native_map.generate_waypoints = lambda spacing: [wrong]
    with pytest.raises(ValueError, match='original driving lane'):
        scene.sampled_map(native_map, route, route['map_opendrive_sha256'])
    duplicate = world.waypoint(2); duplicate.lane_width = 4.
    native_map.generate_waypoints = lambda spacing: [world.waypoint(2), duplicate]
    with pytest.raises(ValueError, match='conflicting'):
        scene.sampled_map(native_map, route, route['map_opendrive_sha256'])


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(schema_version='sensor-scene/99'),
    lambda v: v.update(coordinate_convention='unqualified'),
    lambda v: v.update(fidelity='physically_calibrated'),
    lambda v: v['source'].update(configuration_revision=9),
    lambda v: v['source'].update(trace_sha256='0'*64),
    lambda v: v['map']['lane_segments'][0].update(lane_id=-2),
    lambda v: v['map']['lane_segments'][0]['points'][0].update(lane_width_m=0),
    lambda v: v['map']['lane_segments'][0]['points'][1].update(s_m=90),
    lambda v: v['map'].update(sample_count=1),
    lambda v: v['map']['sampling'].update(roi_margin_m=100),
    lambda v: v['ego']['native_bounding_box'].update(dimensions_m=[5., 2., 2.]),
    lambda v: v['ego']['native_bounding_box']['local_transform']['matrix'][0].__setitem__(3, 9.),
    lambda v: v['frames'][0].update(frame=999),
    lambda v: v['frames'][0]['ego_world_transform']['matrix'][0].__setitem__(3, 9.),
    lambda v: v['frames'][0]['cameras'][0]['sensor_world_transform']['matrix'][0].__setitem__(3, 9.),
    lambda v: v['cameras'][0]['mount_native']['position_m'].__setitem__(0, 2.),
])
def test_rehashed_scene_cannot_replace_pinned_source_geometry_or_original_response(tmp_path, mutation):
    spec, probe, manifest, folder, _ = captured(tmp_path)
    archive, receipt = rewritten(folder, manifest, 'sensor-scene.json', mutation)
    with pytest.raises(ValueError):
        validate_feedback_bundle(archive, receipt, spec, folder.name, probe)


def test_xodr_tamper_is_rejected_even_with_rehashed_original_descriptor(tmp_path):
    spec, probe, manifest, folder, _ = captured(tmp_path)
    data = (folder/'map.xodr').read_bytes().replace(b'length="1000"', b'length="2000"')
    (folder/'map.xodr').write_bytes(data)
    entry = next(e for e in manifest['artifacts'] if e['name'] == 'map.xodr')
    manifest['native_bytes'] += len(data) - entry['size_bytes']
    entry.update(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    archive, receipt = bundle(folder, manifest)
    with pytest.raises(ValueError, match='OpenDRIVE'):
        validate_feedback_bundle(archive, receipt, spec, folder.name, probe)


def test_missing_scene_or_unreferenced_artifact_does_not_become_a_complete_bundle(tmp_path):
    spec, probe, manifest, folder, _ = captured(tmp_path)
    del manifest['scene_artifact']
    archive, receipt = bundle(folder, manifest)
    with pytest.raises(ValueError, match='missing'):
        validate_feedback_bundle(archive, receipt, spec, folder.name, probe)


def test_validated_scene_and_xodr_publish_through_existing_managed_handler_outputs(tmp_path, monkeypatch):
    spec, probe, manifest, folder, _ = captured(tmp_path)
    archive, receipt = bundle(folder)
    handler = configured(monkeypatch)
    request = ExperimentCreate(name='Mocked original scene publication', kind='sensor_capture', adapter_id='carla-feedback-sensor', input=specification(False), seed=42)
    with patch.object(handler, '_probe', return_value=probe):
        prepared = handler.prepare(request)
    process = NS(communicate=lambda data, timeout: (native.canonical(receipt), b''), returncode=0)
    def transfer(command, **kwargs):
        assert command[0] == 'scp'
        shutil.copyfile(archive, command[-1])
        return NS(returncode=0, stdout=b'', stderr=b'')
    with (patch.object(handler, '_probe', return_value=probe), patch('axiom_carla.feedback.uuid.uuid4', return_value=NS(hex='unit')),
          patch('axiom_carla.feedback.subprocess.Popen', return_value=process), patch('axiom_carla.feedback.subprocess.run', side_effect=transfer)):
        result = handler.execute(prepared, tmp_path/'published', lambda: False)
    outputs = {artifact.name: artifact for artifact in result.artifacts}
    assert outputs['sensor-scene.json'].path.read_bytes() == (folder/'sensor-scene.json').read_bytes()
    assert outputs['map.xodr'].path.read_bytes() == (folder/'map.xodr').read_bytes()
    assert result.summary['scene_sha256'] == manifest['scene_artifact']['sha256']
    assert result.summary['map_sha256'] == probe['map_opendrive_sha256']
    assert result.summary['evaluation_status'] == 'not_evaluated'
    assert result.fidelity == 'actual_carla_reference_feedback_rgb'


@pytest.mark.parametrize('field', ['adapter_script_sha256', 'native_script_sha256', 'native_support_script_sha256', 'native_scene_script_sha256'])
def test_execution_rejects_frozen_local_adapter_or_native_dependency_changes_before_dispatch(tmp_path, monkeypatch, field):
    handler = configured(monkeypatch); spec = specification(); probe = native.probe(World().client(), spec)
    request = ExperimentCreate(name='Mocked scene dependency contract', kind='sensor_capture', adapter_id='carla-feedback-sensor', input=specification(False), seed=42)
    with patch.object(handler, '_probe', return_value=probe):
        prepared = handler.prepare(request)
    prepared[field] = '0'*64
    with patch.object(handler, '_probe') as remote, patch('axiom_carla.feedback.subprocess.Popen') as dispatch:
        with pytest.raises(ValueError, match='changed after preview'):
            handler.execute(prepared, tmp_path/'attempt', lambda: False)
        remote.assert_not_called(); dispatch.assert_not_called()


def test_prepare_pins_scene_helper_geometry_and_limits_without_running_native(monkeypatch):
    handler = configured(monkeypatch); probe = native.probe(World().client(), specification())
    request = ExperimentCreate(name='Mocked scene preview', kind='sensor_capture', adapter_id='carla-feedback-sensor', input=specification(False), seed=42)
    with patch.object(handler, '_probe', return_value=probe), patch('axiom_carla.feedback.subprocess.Popen') as dispatch:
        prepared = handler.prepare(request)
    assert prepared['native_scene_script_sha256'] == native.sha_file(scene.__file__)
    assert prepared['workload']['scene_geometry_sample_count'] == probe['scene_map_summary']['sample_count']
    dispatch.assert_not_called()
    probe = copy.deepcopy(probe); probe['native_scene_script_sha256'] = '0'*64
    with patch.object(handler, '_probe', return_value=probe), pytest.raises(ValueError, match='installed adapter'):
        handler.prepare(request)


def test_missing_or_nonfinite_dimensions_fail_closed_without_guessing_vehicle_size():
    with pytest.raises(ValueError, match='no default'):
        scene.bounding_box({'position_m': [0,0,0], 'rotation_deg': [0,0,0], 'matrix': [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}, [0., 1., 1.])
    with pytest.raises(ValueError):
        scene.bounding_box({'position_m': [0,0,0], 'rotation_deg': [0,0,0], 'matrix': [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}, [float('nan'), 1., 1.])


@pytest.mark.parametrize('data', [b'<OpenDRIVE/>', b'<Wrong><road/></Wrong>', b'<!DOCTYPE OpenDRIVE [<!ENTITY e SYSTEM "file:///private">]><OpenDRIVE/>'])
def test_original_map_must_be_bounded_valid_opendrive_with_no_external_entity(data):
    with pytest.raises(ValueError):
        scene.road_index(data)
