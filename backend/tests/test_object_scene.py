"""Authored contract checks; fixtures are not evidence of a simulator execution."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ade2e.object_scene import (ObjectScene, build_object_scene, derive_events, map_geometry,
                              sha256, trajectory_preview, validate_native_frames)
from ade2e.object_sim import ADAPTER_VERSION, PINNED_BINARY_SHA256, EsminiExperimentHandler
from ade2e.scenario_v2_schemas import ScenarioV2
from ade2e.scenario_v2_templates import templates
from ade2e.scenario_v2_xml import canonical_bytes, digest, import_xml, road_xml, scenario_xml


def state(identity='ego', **values):
    return {'id': identity, 'x': 0., 'y': 0., 'z': 0., 'heading_rad': 0., 'speed_mps': 8.,
            'vx_mps': 8., 'vy_mps': 0., 'ax_mps2': 0., 'ay_mps2': 0., 'bb_x': 1.4, 'bb_y': 0.,
            'length_m': 4.5, 'width_m': 1.8, 'lane_id': -1, 'lane_offset_m': 0., **values}


def snapshot_native():
    scenario = deepcopy(templates()[1]['scenario'])
    parsed = ScenarioV2.model_validate(scenario)
    source = {'scenario_id': 'scenario-fixed', 'scenario_revision': 2, 'scenario_manifest_hash': 'a'*64,
              'configuration_hash': digest(scenario), 'scenario': scenario, 'seed': 42,
              'adapter_version': ADAPTER_VERSION, 'runner_sha256': 'b'*64,
              'engine_binary_sha256': PINNED_BINARY_SHA256,
              'native_request': {'schema_version': 'esmini-native-request/1', 'scenario_xml': scenario_xml(parsed),
                                 'road_xml': road_xml(parsed), 'duration_s': parsed.duration_s,
                                 'timestep_s': parsed.timestep_s, 'seed': 42, 'actor_ids': ['ego', 'other']}}
    frames = [{'time_s': t, 'actors': [state(x=5.+t*8., y=-1.75),
                                     state('other', x=22.+t*8., y=-5.25, lane_id=-2)]} for t in (0., 8.)]
    native = {'schema_version': 'esmini-native-result/1', 'engine_version': 'v3.8.2', 'engine_build': '6400',
              'runner_sha256': source['runner_sha256'], 'seed': 42, 'binary_sha256': PINNED_BINARY_SHA256,
              'frames': frames, 'native_csv': 'authored response fixture only\n', 'native_log': '',
              'scenario_xml_sha256': sha256(source['native_request']['scenario_xml']),
              'road_xml_sha256': sha256(source['native_request']['road_xml'])}
    native['native_csv_sha256'] = sha256(native['native_csv'])
    trace = canonical_bytes({'schema_version': 'object-trace/1', 'coordinate_system': parsed.coordinate_system, 'frames': frames})
    return source, native, trace


def test_map_uses_exact_source_lane_width_length_and_rejects_unsupported_geometry():
    scenario = ScenarioV2.model_validate(templates()[0]['scenario'])
    scenario.map.length_m, scenario.map.lane_width_m = 234., 4.2
    source = road_xml(scenario)
    result = map_geometry(scenario, source)
    assert result['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()
    assert result['reference_line'][-1] == {'x': 234., 'y': 0., 'z': 0.}
    assert [lane['centerline'][0]['y'] for lane in result['lanes']] == [-2.1, -6.300000000000001]
    assert result['lanes'][1]['right_boundary'][-1]['y'] == -8.4
    for altered in (source.replace('<line />', '<arc curvature="0.01" />'),
                    source.replace('length="234.0"', 'length="235.0"', 1),
                    source.replace('</road>', '<elevationProfile><elevation s="0" a="1" b="0" c="0" d="0" /></elevationProfile></road>'),
                    source.replace('</OpenDRIVE>', '<road id="2" /></OpenDRIVE>'),
                    '<!DOCTYPE OpenDRIVE>' + source):
        with pytest.raises(ValueError):
            map_geometry(scenario, altered)


def test_rotated_native_bbox_offset_drives_sampled_collision_bounds():
    scenario = templates()[1]['scenario']
    ego = state(heading_rad=1.5707963267948966, length_m=4., width_m=2.)
    other = state('other', y=4., bb_x=0., length_m=4., width_m=2.)
    frames = [{'time_s': 0., 'actors': [ego, other]}, {'time_s': .1, 'actors': [ego, other]},
              {'time_s': .2, 'actors': [ego, {**other, 'y': 10.}]}]
    events = derive_events(scenario, frames, '', 'c'*64, 'd'*64)
    collision, = [event for event in events if event['kind'] == 'collision']
    assert collision['time_s'] == 0. and collision['end_time_s'] == .1
    assert collision['provenance']['frame_indices'] == [0, 1]
    assert collision['details']['open_at_start'] and not collision['details']['open_at_end']
    assert collision['details']['after_time_s'] == .2
    assert collision['details']['coordinate_plane'] == 'world_xy'
    without_offset = deepcopy(frames)
    for frame in without_offset:
        frame['actors'][0]['bb_x'] = 0.
    assert not [event for event in derive_events(scenario, without_offset, '', 'c'*64, 'd'*64) if event['kind'] == 'collision']


def test_engine_event_completion_does_not_create_unobserved_lane_transition():
    scenario = templates()[1]['scenario']
    frames = [{'time_s': t, 'actors': [state(y=-1.75), state('other', x=30., y=-1.75)]} for t in (0., .1, .2)]
    log = ('[0.100] [info] other_0_event standbyState -> startTransition -> runningState\n'
           '[0.100] [info] other_0_event runningState -> endTransition -> completeState\n'
           '[0.200] [warn] Collision between ego and other\n'
           '[0.200] [warn] Collision between ego and other dissolved\n')
    events = derive_events(scenario, frames, log, 'c'*64, sha256(log))
    assert not [event for event in events if event['kind'] == 'lane_transition']
    logged = [event for event in events if event['provenance']['source'] == 'native_log']
    assert [event['provenance']['line_numbers'] for event in logged] == [[1], [2], [3], [4]]
    assert logged[1]['details']['engine_state'] == 'completeState'
    assert 'not_observed_behavior_completion' in logged[1]['details']['interpretation']
    changed = deepcopy(frames)
    changed[1]['actors'][1].update(lane_id=-2, y=-5.25, speed_mps=0.)
    observed = derive_events(scenario, changed, '', 'c'*64, 'd'*64)
    assert observed[0]['provenance']['frame_indices'] == [0, 1]
    stop, = [event for event in observed if event['kind'] == 'stopped']
    assert stop['time_s'] == .1 and stop['details']['previous_time_s'] == 0.
    changed[0]['actors'][1]['speed_mps'] = -8.
    assert len([event for event in derive_events(scenario, changed, '', 'c'*64, 'd'*64) if event['kind'] == 'stopped']) == 1


def test_scene_preserves_full_original_native_states_and_actor_source_metadata():
    snapshot, native, trace = snapshot_native()
    native['native_log'] = '[ ] ignored\n[] [info] Add and deactivate new object "ego" (id 0)\n'
    scene = build_object_scene(snapshot, native, trace)
    assert ObjectScene.model_validate(scene).schema_version == 'object-scene/1'
    assert scene['frames'] == native['frames']
    assert scene['source']['object_trace_sha256'] == sha256(trace)
    assert scene['source']['native_log_sha256'] == sha256(native['native_log'])
    assert scene['source']['scenario_revision'] == 2
    assert scene['actors'][0]['metadata']['native_entity_id'] == 0
    assert scene['actors'][0]['metadata']['native_entity_id_provenance']['line_number'] == 2
    assert scene['actors'][0]['metadata']['height_source'] == 'authored_scenario_not_native_observation'
    assert scene['frames'][0]['actors'][0]['bb_x'] == 1.4
    assert 'bb_z' not in scene['frames'][0]['actors'][0]
    assert scene['actors'][1]['authored_intent']['actions'][0]['kind'] == 'lane_change'
    assert not scene['events']
    changed = json.loads(trace)
    changed['frames'][0]['actors'][0]['x'] += 1.
    with pytest.raises(ValueError, match='full original object trace'):
        build_object_scene(snapshot, native, canonical_bytes(changed))
    wrong = deepcopy(native)
    wrong['road_xml_sha256'] = '0'*64
    with pytest.raises(ValueError, match='source hashes'):
        build_object_scene(snapshot, wrong, trace)


@pytest.mark.parametrize('identity', ['configuration_hash', 'scenario_xml', 'seed', 'runner_sha256', 'engine_binary_sha256'])
def test_scene_rejects_receipt_or_authored_identity_drift(identity):
    snapshot, native, trace = snapshot_native()
    if identity == 'scenario_xml': snapshot['native_request']['scenario_xml'] += '\n'
    elif identity == 'seed': snapshot['seed'] += 1
    else: snapshot[identity] = '0'*64
    with pytest.raises(ValueError, match='identity'):
        build_object_scene(snapshot, native, trace)


def test_typed_scene_rejects_unverifiable_hash_or_event_provenance():
    snapshot, native, trace = snapshot_native()
    scene = build_object_scene(snapshot, native, trace)
    scene['source']['object_trace_sha256'] = 'unverifiable'
    with pytest.raises(ValueError): ObjectScene.model_validate(scene)
    scene = build_object_scene(snapshot, native, trace)
    scene['map']['source_sha256'] = 'c'*64
    with pytest.raises(ValueError, match='map hash'): ObjectScene.model_validate(scene)
    scene = build_object_scene(snapshot, native, trace)
    scene['events'] = [{'kind': 'stopped', 'actor_ids': ['ego'], 'time_s': 1., 'details': {},
                       'provenance': {'source': 'native_trace', 'artifact': 'object-trace.json',
                                      'sha256': scene['source']['object_trace_sha256'],
                                      'method': 'sampled_speed_threshold_crossing', 'frame_indices': [2]}}]
    with pytest.raises(ValueError, match='outside the full trace'): ObjectScene.model_validate(scene)
    scene = build_object_scene(snapshot, native, trace)
    scene['events'] = [{'kind': 'stopped', 'actor_ids': ['ego'], 'time_s': 1., 'details': {},
                        'provenance': {'source': 'native_log', 'artifact': 'object-trace.json',
                                       'sha256': 'c'*64, 'method': 'esmini_event_state_log', 'frame_indices': [1], 'line_numbers': []}}]
    with pytest.raises(ValueError): ObjectScene.model_validate(scene)


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'clock', 'nonfinite', 'string', 'unknown'])
def test_incomplete_native_state_fails_closed(mutation):
    _, native, _ = snapshot_native()
    frames = deepcopy(native['frames'])
    if mutation == 'missing': del frames[0]['actors'][0]['bb_x']
    elif mutation == 'duplicate': frames[0]['actors'].append(frames[0]['actors'][0])
    elif mutation == 'clock': frames[1]['time_s'] = frames[0]['time_s']
    elif mutation == 'nonfinite': frames[1]['actors'][1]['x'] = float('nan')
    elif mutation == 'string': frames[1]['actors'][1]['x'] = '10'
    elif mutation == 'unknown': frames[1]['actors'][1]['invented_native_height'] = 1.5
    with pytest.raises(ValueError): validate_native_frames(frames, ['ego', 'other'])


@pytest.mark.parametrize('count', [2, 63, 64, 65, 82, 1502])
def test_preview_preserves_first_and_last_original_sample_without_modifying_trace(count):
    frames = [{'time_s': index*.02, 'actors': [state(x=float(index))]} for index in range(count)]
    original = deepcopy(frames)
    preview = trajectory_preview(frames)
    assert len(preview) == min(count, 64)
    assert preview[0] is frames[0] and preview[-1] is frames[-1]
    assert all(first['time_s'] < second['time_s'] for first, second in zip(preview, preview[1:]))
    assert frames == original


def test_named_traffic_template_contains_separate_supported_interactions_and_roundtrips():
    scenario = ScenarioV2.model_validate(next(item['scenario'] for item in templates() if item['id'] == 'traffic_interaction'))
    assert scenario.template == 'traffic_interaction' and len(scenario.actors) == 5 and scenario.duration_s <= 12
    assert import_xml(scenario_xml(scenario)) == scenario
    actors = {actor.id: actor for actor in scenario.actors}
    change = actors['lane_changer']
    assert change.pose.y == -1.5*scenario.map.lane_width_m
    assert change.actions[0].target_lane == -1
    assert actors['lead'].actions[0].kind == 'stop'
    path = actors['pedestrian'].actions[0].path
    assert path[0].y <= -2*scenario.map.lane_width_m and path[-1].y > 0
    assert path[0].time_s == actors['pedestrian'].actions[0].trigger.value
    assert path[-1].time_s <= scenario.duration_s


def test_new_handler_publishes_scene_and_fulltrace_hashes_and_rejects_old_version(tmp_path):
    snapshot, native, _ = snapshot_native()
    handler = EsminiExperimentHandler(command=['authored-contract-only'])
    snapshot['runner_sha256'] = sha256(handler.runner.read_bytes())
    snapshot['command_hash'] = digest(handler.command)
    native['runner_sha256'] = snapshot['runner_sha256']
    def contract_response(command, **kwargs):
        kwargs['stdout'].write(canonical_bytes(native))
        return SimpleNamespace(returncode=0, stderr=b'')
    with patch('ade2e.object_sim.subprocess.run', side_effect=contract_response):
        output = handler.execute(snapshot, tmp_path, lambda: False)
    files = {artifact.name: artifact.path for artifact in output.artifacts}
    assert output.summary['adapter_version'] == ADAPTER_VERSION
    assert json.loads(files['object-scene.json'].read_bytes())['frames'] == native['frames']
    assert json.loads(files['object-trace.json'].read_bytes())['frames'] == native['frames']
    for name, expected in output.summary['original_artifact_hashes'].items():
        assert sha256(files[name].read_bytes()) == expected
    assert output.summary['object_scene']['sha256'] == sha256(files['object-scene.json'].read_bytes())
    assert output.summary['trajectory_preview'][-1] == native['frames'][-1]
    old = deepcopy(snapshot)
    old['adapter_version'] = '0.1.0+esmini3.8.2'
    with patch('ade2e.object_sim.subprocess.run') as process, pytest.raises(RuntimeError, match='prior sealed outputs'):
        handler.execute(old, tmp_path/'old', lambda: False)
    process.assert_not_called()
    unsupported = deepcopy(snapshot)
    unsupported['native_request']['road_xml'] = unsupported['native_request']['road_xml'].replace('<line />', '<arc curvature="0.1" />')
    with patch('ade2e.object_sim.subprocess.run') as process, pytest.raises(RuntimeError, match='pinned scenario'):
        handler.execute(unsupported, tmp_path/'unsupported', lambda: False)
    process.assert_not_called()
