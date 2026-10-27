"""Authoring and orchestration contracts; fixtures do not run a native engine."""
import copy
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_object_beta as beta


def native_inputs():
    nodes = {}
    for lane in (0, 1):
        for index in range(41):
            node = {'id': f'{lane}:{index}', 'x': index * 5, 'y': lane * 3.5, 'z': 0,
                'heading_rad': 0, 'road_id': 1, 'section_id': 0, 'lane_id': -lane - 1,
                'lane_width_m': 3.5, 'next_ids': [f'{lane}:{index + 1}'] if index < 40 else [],
                'is_junction': False}
            if lane == 0:
                node.update(left_id=f'1:{index}', lane_change_left=True)
            else:
                node.update(right_id=f'0:{index}', lane_change_right=True)
            nodes[node['id']] = node
    def turn(name, sign):
        points = [(i * 5, sign * 30, 0) for i in range(5)] + [
            (20, sign * (30 + i * 5), sign * math.pi / 2) for i in range(1, 6)]
        result = []
        for index, (x, y, heading) in enumerate(points):
            node = {'id': f'{name}:{index}', 'x': x, 'y': y, 'z': 0, 'heading_rad': heading,
                'road_id': 2 if sign < 0 else 3, 'section_id': 0, 'lane_id': -1,
                'lane_width_m': 3.5, 'is_junction': True,
                'next_ids': [f'{name}:{index + 1}'] if index < len(points) - 1 else []}
            nodes[node['id']] = node; result.append(node)
        return result
    left, right = turn('left', -1), turn('right', 1)
    paths = {'straight': [nodes[f'0:{i}'] for i in range(10)],
             'cut_in': [nodes[f'0:{i}'] for i in range(16)],
             'left_turn': left, 'crossing': [nodes[f'1:{i}'] for i in range(14)]}
    native = {'id': 'map_fixture', 'revision': 2, 'sha256': 'a' * 64,
              'definition': {'nodes': list(nodes.values()), 'signals': []}}
    vehicle = {'id': 'asset_fixture', 'revision': 3, 'sha256': 'b' * 64}
    def actor(identity, route):
        return {'id': identity, 'role': 'ego' if identity == 'ego' else 'other', 'kind': 'vehicle',
            'asset': beta.ref(vehicle), 'pose': beta.pose(route[0]), 'route': [beta.pose(p) for p in route],
            'destination': beta.pose(route[-1]), 'speed_mps': 4, 'controller': {'kind': 'reactive'},
            'planner': {'allow_lane_changes': False}, 'behavior': {'risk_mode': False, 'actions': []}}
    ego, lead = actor('ego', right), actor('lead', right[3:])
    lead['behavior']['actions'] = [{'id': 'lead_wait', 'kind': 'wait',
        'trigger': {'kind': 'time', 'value': 2}, 'duration_s': 6, 'timeout_s': 14}]
    source = {'scenario': {'schema_version': 'scenario-v2/3', 'name': 'Authored right fixture',
        'seed': 42, 'map': beta.ref(native), 'duration_s': 24, 'timestep_s': .05, 'actors': [ego, lead]},
        'actor_roles': {'ego': 'ego', 'lead': 'lead'}}
    return native, vehicle, paths, source


def pose_key(point):
    return tuple(point[key] for key in ('x', 'y', 'z', 'heading_rad'))


def assert_definitions(native, vehicle, values):
    nodes = {n['id']: n for n in native['definition']['nodes']}
    by_pose = {pose_key(n): n for n in nodes.values()}
    assert set(values) == set(beta.PRESETS)
    for value in values.values():
        scene = value['scenario']
        assert scene['map'] == beta.ref(native) and scene['seed'] == 42
        assert scene['timestep_s'] == .05
        ticks = round(scene['duration_s'] / scene['timestep_s'])
        assert ticks <= 2400
        assert {sensor['id'] for sensor in scene['sensors']} == {'front', 'chase'}
        assert all(sensor['actor_id'] == 'ego' for sensor in scene['sensors'])
        camera_bytes = sum(math.ceil(ticks / (sensor['period_s'] / scene['timestep_s'])) * sensor['width'] * sensor['height'] * 4 for sensor in scene['sensors'])
        assert camera_bytes <= 2 * 1024 ** 3 - 128 * 1024 ** 2
        for actor in scene['actors']:
            assert actor['asset'] == beta.ref(vehicle)
            assert actor['pose'] == actor['route'][0]
            assert actor['controller']['kind'] == 'reactive'
            assert 0 <= actor['speed_mps'] <= 12
            route = [by_pose[pose_key(point)] for point in actor['route']]
            assert len(route) >= 2
            assert all(second['id'] in first['next_ids'] for first, second in zip(route, route[1:]))
            assert pose_key(actor['destination']) in by_pose
            if actor['id'] != 'intruder':
                assert actor['destination'] == actor['route'][-1]
    cut = values['cut_in']['scenario']['actors'][1]
    start = by_pose[pose_key(cut['pose'])]
    target = nodes[cut['behavior']['actions'][0]['target_lane_node_id']]
    assert cut['behavior']['risk_mode'] and cut['planner']['allow_lane_changes']
    assert cut['behavior']['actions'][0]['kind'] == 'cut_in'
    assert any(start.get(side + '_id') == target['id'] and start.get('lane_change_' + side) for side in ('left', 'right'))
    assert abs(math.atan2(math.sin(start['heading_rad'] - target['heading_rad']), math.cos(start['heading_rad'] - target['heading_rad']))) < .2
    for key, sign in [('urban_left_turn', -1), ('urban_right_turn', 1)]:
        route = values[key]['scenario']['actors'][0]['route']
        yaw = sum(math.atan2(math.sin(b['heading_rad'] - a['heading_rad']), math.cos(b['heading_rad'] - a['heading_rad'])) for a, b in zip(route, route[1:]))
        assert sign * yaw > .85


def test_templates_preserve_directed_graph_pins_destinations_and_capture_budget():
    native, vehicle, paths, source = native_inputs()
    original = copy.deepcopy((native, vehicle, paths, source))
    values = beta.definitions(native, vehicle, paths, source)
    assert_definitions(native, vehicle, values)
    assert (native, vehicle, paths, source) == original


def test_cut_in_requires_observed_legal_adjacency_permission():
    native, vehicle, paths, source = native_inputs()
    for node in native['definition']['nodes']:
        node['lane_change_left'] = node['lane_change_right'] = False
    with pytest.raises(ValueError, match='legal adjacent-lane permission'):
        beta.definitions(native, vehicle, paths, source)


def test_forward_preserves_native_edges_and_stops_at_a_cycle():
    a = {'id': 'a', 'x': 0, 'y': 0, 'heading_rad': 0, 'next_ids': ['b', 'c']}
    b = {'id': 'b', 'x': 5, 'y': 0, 'heading_rad': 0, 'next_ids': ['a']}
    c = {'id': 'c', 'x': 0, 'y': 5, 'heading_rad': math.pi / 2, 'next_ids': []}
    assert beta.forward({n['id']: n for n in (a, b, c)}, a, 100) == [a, b]


@pytest.mark.parametrize('no_publish',[False,True])
def test_rehearsal_submits_the_named_single_variable_sides_and_pins_completed_b(tmp_path,no_publish):
    class API:
        def __init__(self):
            self.calls = []; self.scenarios = {}; self.jobs = {}; self.preview = None
        def request(self, path, body=None, key=None):
            self.calls.append((path, body, key))
            if path.endswith('/preview'):
                self.preview = {'name': path.split('/')[-2], 'parameters': body['parameters'], 'seed': 42}
                return {'scenario': self.preview}
            if path == '/scenario-v2':
                identity = 'scenario_' + str(len(self.scenarios))
                value = {'id': identity, 'revision': 1, 'configuration_hash': 'a' * 64, 'manifest_hash': 'b' * 64,
                         'scenario': body['scenario']}
                self.scenarios[identity] = value
                return value
            if path == '/experiments':
                identity = 'experiment_' + str(len(self.jobs))
                self.jobs[identity] = body
                return {'id': identity, 'preview_hash': 'c' * 64}
            if path.endswith('/executions'):
                return {'id': 'job_' + str(len(self.jobs))}
            if path.startswith('/experiments/executions/'):
                return {'status': 'completed', 'error': None, 'manifest_hash': 'd' * 64, 'outcome_uncertain': False,
                        'summary': {'scope': 'authored_test_only','evaluation_status':'evaluated','verification_verdict':'fail'}}
            if '/behavior?' in path:
                saved=self.scenarios['scenario_'+str(len(self.scenarios)-1)]
                pin=beta.scenario_ref(saved);identity=path.split('/')[3]
                return {'schema_version':'object-sim-behavior/1','project_id':'project_fixture',
                    'execution_id':identity,'scenario_ref':pin,'status':'available',
                    'source':{'execution_id':identity,'scenario_ref':pin,'manifest_hash':'d'*64,'artifact_sha256':'e'*64},
                    'actors':[],'ego_outcome':{'status':'stopped'}}
            if path.startswith('/object-sim/presets?'):
                return {'items': [{'id': preset, 'binding_revision': 1} for preset in beta.PRESETS]}
            if path.endswith('/bind'):
                return {'revision': 2}
            raise AssertionError(path)
    prepared = tmp_path / 'prepared.json'
    prepared.write_text(json.dumps({'api': 'http://fixture/api/v1', 'complete': True, 'project_id': 'project_fixture',
        'presets': {preset: {'actor_roles': {'ego': 'ego'}} for preset in beta.PRESETS}}))
    args = SimpleNamespace(prepared=prepared, api='http://fixture/api/v1', output=tmp_path / 'result.json', presets=None, timeout=1,no_publish=no_publish)
    api = API(); result = beta.rehearse(args, api)
    previews = [body['parameters'] for path, body, _ in api.calls if path.endswith('/preview')]
    assert previews == [{'lead_wait_s': 2}, {'lead_wait_s': 4}, {'cut_in_start_s': 1}, {'cut_in_start_s': 2},
                        {'crossing_speed_mps': 4}, {'crossing_speed_mps': 3}, {'lead_wait_s': 0}, {'lead_wait_s': 6}]
    assert result == {'complete': True, 'trials': 8}
    pins = [body for path, body, _ in api.calls if path.endswith('/bind')]
    receipt=json.loads(args.output.read_text())
    assert receipt['complete'] is receipt['execution_complete'] is True
    assert receipt['completion_scope']=='execution_completion_only_not_behavior_acceptance'
    assert receipt['behavior_review_status']=='pending'
    assert receipt['publication_mode']==('review_only' if no_publish else 'automatic_after_execution')
    assert len(pins)==(0 if no_publish else 4)
    assert receipt['published_presets']==([] if no_publish else list(beta.PRESETS))
    for trial in receipt['trials']:
        assert trial['verification_verdict']=='fail', 'Completed jobs must not replace a failed measured verdict'
        assert trial['evaluation_status']=='evaluated'
        assert trial['behavior_report']['scenario_ref']==trial['scenario_ref']
        assert trial['behavior_report']['execution_id']==trial['execution_id']
        assert trial['behavior_review_status']=='pending'
    for index, pin in enumerate(pins):
        assert pin['saved_execution_id'] == 'job_' + str(2 * index + 2)
        assert pin['saved_comparison_execution_id'] == 'job_' + str(2 * index + 1)
        assert pin['scenario_ref']['id'] == 'scenario_' + str(2 * index + 1)


@pytest.mark.parametrize('failure',['uncertain','wrong_scenario','wrong_artifact_source'])
def test_rehearsal_stops_before_publication_for_uncertainty_or_report_identity_mismatch(tmp_path,failure):
    pin={'id':'saved','revision':1,'configuration_hash':'a'*64,'manifest_hash':'b'*64}
    prepared=tmp_path/'prepared.json';output=tmp_path/'result.json'
    prepared.write_text(json.dumps({'api':'http://fixture/api/v1','complete':True,'project_id':'project_fixture','presets':{}}))
    output.write_text(json.dumps({'project_id':'project_fixture','trials':[{
        'key':'lead_stop_start:A','preset':'lead_stop_start','side':'A','execution_id':'job_fixture','scenario_ref':pin}]}))
    calls=[]
    class API:
        def request(self,path,body=None,key=None):
            calls.append(path)
            if path=='/experiments/executions/job_fixture':
                return {'status':'completed','outcome_uncertain':failure=='uncertain','manifest_hash':'c'*64,
                        'summary':{'evaluation_status':'evaluated','verification_verdict':'pass'}}
            if path=='/object-sim/executions/job_fixture/behavior?project_id=project_fixture':
                return {'schema_version':'object-sim-behavior/1','project_id':'project_fixture',
                    'execution_id':'job_fixture','scenario_ref':{**pin,'revision':2} if failure=='wrong_scenario' else pin,
                    'status':'unavailable' if failure=='uncertain' else 'available',
                    'source':{'execution_id':'job_fixture','scenario_ref':pin,
                        'manifest_hash':'wrong' if failure=='wrong_artifact_source' else 'c'*64,'artifact_sha256':'d'*64}}
            raise AssertionError(path)
    args=SimpleNamespace(prepared=prepared,api='http://fixture/api/v1',output=output,presets=['lead_stop_start'],timeout=1,no_publish=False)
    with pytest.raises((RuntimeError,ValueError),match='uncertain|differs'):
        beta.rehearse(args,API())
    receipt=json.loads(output.read_text());trial=receipt['trials'][0]
    assert trial['execution_complete'] is (failure!='uncertain')
    assert trial['behavior_report']['execution_id']=='job_fixture'
    assert trial['behavior_review_status']=='pending'
    assert not any(path.endswith('/bind') for path in calls)


def test_cached_native_catalog_templates_when_acceptance_inputs_are_available():
    root = Path(__file__).resolve().parents[2] / '.runtime/object-beta-20261010'
    if not all((root / name).is_file() for name in ('prepared.json', 'source.json', 'beta-paths.json')):
        pytest.skip('Optional locally retained native catalog acceptance inputs are not installed')
    prepared = json.loads((root / 'prepared.json').read_text())
    source = json.loads((root / 'source.json').read_text())
    paths = json.loads((root / 'beta-paths.json').read_text())
    native = prepared['catalogs']['maps/carla-town10hd-opt-beta']
    vehicle = prepared['catalogs']['assets/vehicle-toyota-prius']
    assert_definitions(native, vehicle, beta.definitions(native, vehicle, paths, source['right_turn']))
