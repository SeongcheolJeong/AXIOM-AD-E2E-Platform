"""Authored local recipe/API doubles only; these tests never run CARLA or HTTP."""
import copy
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import urllib.parse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import object_sim_urban_interaction as recipe


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    points = [(x, 0, 0) for x in range(0, 20, 2)]
    points += [(20+10*math.sin(i*math.pi/16), 10-10*math.cos(i*math.pi/16), i*math.pi/16) for i in range(9)]
    points += [(30, 10+y, math.pi/2) for y in range(2, 62, 2)]
    nodes = [{'id': f'n{i:02}', 'x': x, 'y': y, 'z': 0, 'heading_rad': h,
        'road_id': 1, 'section_id': 0, 'lane_id': -1, 'is_junction': 10 <= i <= 18,
        'next_ids': [f'n{i+1:02}'] if i < len(points)-1 else []} for i, (x,y,h) in enumerate(points)]
    # Coincident junction node is not an edge successor of n09. A nearest-only
    # lookup would choose the wrong native identity and lose the original edge.
    nodes.insert(0, {**nodes[10], 'id': 'coincident-junction-node'})
    definition = {'engine': 'carla', 'native_name': 'Carla/Maps/Town10HD_Opt', 'nodes': nodes,
        'signals': [{'id': 'native-light', 'kind': 'traffic_light'}], 'rights': {'license': 'authored fixture'}}
    native_map = {'id': recipe.MAP_ID, 'revision': 1, 'sha256': 'a'*64, 'definition': definition}
    vehicle = {'id': 'vehicle-toyota-prius', 'revision': 1, 'sha256': 'b'*64,
        'definition': {'blueprint_id': 'vehicle.toyota.prius', 'rights': {'license': 'authored fixture'}}}
    source_scenario = {'schema_version': 'scenario-v2/2', 'actors': [{'role': 'ego',
        'route': [recipe.pose(n) for n in nodes[1:24]]}]}
    pin = {'id': 'fixture-source', 'revision': 1, 'configuration_hash': recipe.digest(source_scenario), 'manifest_hash': 'c'*64}
    monkeypatch.setattr(recipe, 'SOURCE_REF', pin)
    source = {'case': 'right_turn', 'map': recipe.ref(native_map), 'scenario': source_scenario, 'saved_scenario': pin}
    args = SimpleNamespace(catalog=tmp_path/'catalog.json', source_suite=tmp_path/'suite.json',
        output=tmp_path/'receipt.json', lead_wait_s=6, duration_s=24, camera_free=False, side='all', timeout=1)
    args.catalog.write_text(json.dumps({'map': definition, 'assets': [vehicle['definition']]}))
    args.source_suite.write_text(json.dumps({'cases': [source]}))
    return args, native_map, vehicle, source


class API:
    url = 'http://authored.test/api/v1'
    def __init__(self, native_map, vehicle):
        self.catalogs = {'maps': native_map, 'assets': vehicle}
        self.calls = []; self.responses = {}; self.scenarios = {}; self.experiments = {}; self.jobs = {}
        self.compatible = True; self.bind_count = 0
        self.preset = {'id': recipe.PRESET, 'ready': False, 'binding_revision': None}

    def items(self, kind, project):
        assert project == 'demo'
        self.calls.append(('list:'+kind, None, None))
        value = copy.deepcopy(self.catalogs[kind])
        if kind == 'maps': value['definition'].pop('nodes')
        return [value]

    def request(self, path, body=None, key=None):
        self.calls.append((path, copy.deepcopy(body), key))
        if key and key in self.responses: return copy.deepcopy(self.responses[key])
        if body and 'project_id' in body: assert body['project_id'] == 'demo'
        if path.startswith(('/maps/', '/assets/')):
            kind = path.split('/')[1]
            assert 'project_id=demo' in path and 'revision=1' in path
            return copy.deepcopy(self.catalogs[kind])
        if path == '/scenario-v2/yaml/validate':
            scenario = json.loads(body['yaml'])
            return {'scenario': scenario, 'configuration_hash': recipe.digest(scenario)}
        if path == '/scenario-v2':
            identity = 'scenario-'+str(len(self.scenarios)+1)
            scenario = copy.deepcopy(body['scenario'])
            result = {'id': identity, 'revision': 1, 'project_id': 'demo', 'scenario': scenario,
                'configuration_hash': recipe.digest(scenario), 'manifest_hash': recipe.digest({'manifest': scenario})}
            self.scenarios[identity] = result
        elif path == '/experiments':
            identity = 'experiment-'+str(len(self.experiments)+1)
            result = {'id': identity, 'preview_hash': 'd'*64}
            self.experiments[identity] = body['input']['scenario_id']
        elif path.startswith('/experiments/experiment-') and path.endswith('/executions'):
            identity = 'job-'+str(len(self.jobs)+1)
            source_id = self.experiments[path.split('/')[2]]
            self.jobs[identity] = {'id': identity, 'project_id': 'demo', 'status': 'completed',
                'manifest_hash': recipe.digest({'job': identity}), 'outcome_uncertain': False,
                'summary': {'verification_verdict': 'not_evaluated', 'camera_frame_count': 720},
                'fixture_scenario_id': source_id}
            result = {'id': identity}
        elif path.startswith('/experiments/executions/'):
            return copy.deepcopy(self.jobs[path.rsplit('/', 1)[1]])
        elif path.startswith('/object-sim/comparison?'):
            query = urllib.parse.parse_qs(path.split('?', 1)[1]); assert query['project_id'] == ['demo']
            result = {'compatibility': {'compatible': self.compatible}}
            for name in ('left', 'right'):
                job = self.jobs[query[name+'_execution_id'][0]]
                result[name] = {'scenario_ref': recipe.scenario_ref(self.scenarios[job['fixture_scenario_id']])}
            return result
        elif path == '/object-sim/presets?project_id=demo':
            return {'items': [copy.deepcopy(self.preset)]}
        elif path == '/object-sim/presets/urban_right_turn/bind':
            assert body['expected_revision'] == 0 and body['actor_roles'] == {'ego': 'ego', 'lead': 'lead'}
            self.bind_count += 1
            self.preset.update(ready=True, binding_revision=1, **{k: body[k] for k in
                ('scenario_ref', 'saved_execution_id', 'saved_comparison_execution_id')})
            return {'revision': 1}
        else:
            pytest.fail('Unexpected API request: '+path)
        self.responses[key] = copy.deepcopy(result)
        return result


def test_pair_changes_only_lead_wait_and_preserves_native_edges_camera_budget(inputs):
    _, native_map, vehicle, source = inputs
    pair, provenance, budget = recipe.scenario_pair(native_map, vehicle, source)
    a, b = pair['A'], pair['B']
    assert a['actors'][0] == b['actors'][0] and not a['actors'][0]['behavior']['actions']
    assert not a['actors'][1]['behavior']['actions']
    changed = copy.deepcopy(b); action = changed['actors'][1]['behavior']['actions'].pop()
    assert changed == a
    assert action == {'id': 'lead_wait', 'kind': 'wait', 'trigger': {'kind': 'time', 'value': 2, 'operator': 'gte'},
        'duration_s': 6, 'timeout_s': 14, 'on_timeout': 'fail'}
    assert a['actors'][0]['route'] == source['scenario']['actors'][0]['route']
    assert provenance['initial_center_spacing_m'] == pytest.approx(12)
    assert provenance['lead_extension_beyond_ego_goal_m'] >= 20
    nodes = {n['id']: n for n in native_map['definition']['nodes']}
    for key in ('ego_route_node_ids', 'lead_route_node_ids'):
        ids = provenance[key]
        assert all(right in nodes[left]['next_ids'] for left, right in zip(ids, ids[1:]))
        assert 'coincident-junction-node' not in ids
    assert a['signals'] == b['signals'] == [{'id': 'native-light', 'initial_state': 'green', 'changes': []}]
    assert budget['frames_by_sensor'] == {'front': 480, 'chase': 240}
    assert budget['frame_count'] == 720 and budget['raw_bytes'] == 1492992000 < budget['raw_limit_bytes']
    assert [(s['width'], s['height'], s['period_s']) for s in b['sensors']] == [(960, 540, .05), (960, 540, .1)]


def test_camera_free_and_bounds_fail_closed(inputs):
    _, native_map, vehicle, source = inputs
    pair, _, budget = recipe.scenario_pair(native_map, vehicle, source, camera_free=True)
    assert not pair['A']['sensors'] and budget['raw_bytes'] == 0
    for wait in (0, 7, True, float('nan')):
        with pytest.raises(ValueError): recipe.scenario_pair(native_map, vehicle, source, lead_wait_s=wait)
    pair, _, _ = recipe.scenario_pair(native_map, vehicle, source)
    pair['B']['duration_s'] = 40
    with pytest.raises(ValueError, match='budget'): recipe.camera_budget(pair['B'])
    bad = copy.deepcopy(native_map); bad['definition']['generated'] = {'source': 'authored'}
    with pytest.raises(ValueError, match='native Town10'): recipe.scenario_pair(bad, vehicle, source)


def test_native_route_disconnection_or_ambiguous_extension_fails(inputs):
    _, native_map, vehicle, source = inputs
    bad = copy.deepcopy(native_map)
    next(n for n in bad['definition']['nodes'] if n['id'] == 'n09')['next_ids'] = []
    with pytest.raises(ValueError, match='every original native graph edge'):
        recipe.scenario_pair(bad, vehicle, source)
    bad = copy.deepcopy(native_map)
    next(n for n in bad['definition']['nodes'] if n['id'] == 'n22')['next_ids'].append('n30')
    with pytest.raises(ValueError, match='unambiguous original native successor'):
        recipe.scenario_pair(bad, vehicle, source)


def test_author_pins_both_variants_and_rejects_retargeting_or_source_changes(inputs):
    args, native_map, vehicle, source = inputs; api = API(native_map, vehicle)
    receipt = recipe.author(args, api, 'demo'); calls = copy.deepcopy(api.calls)
    assert recipe.author(args, api, 'demo') == receipt and api.calls == calls
    assert receipt['source_scenario_ref'] == source['saved_scenario']
    assert all(recipe.digest(s) == receipt['configuration_hashes'][side] for side, s in receipt['scenarios'].items())
    with pytest.raises(ValueError, match='another project'): recipe.run(args, api, 'other')
    api.url = 'http://another-target.test/api/v1'
    with pytest.raises(ValueError, match='another project'): recipe.run(args, api, 'demo')
    api.url = API.url; args.lead_wait_s = 4
    with pytest.raises(ValueError, match='different authoring inputs'): recipe.author(args, api, 'demo')
    assert api.calls == calls and not api.jobs
    source['scenario']['actors'][0]['route'][0]['x'] += 1
    args.source_suite.write_text(json.dumps({'cases': [source]}))
    with pytest.raises(ValueError, match='exact pinned'): recipe.author(args, api, 'demo')


def test_side_only_and_full_resume_never_create_duplicate_jobs(inputs):
    args, native_map, vehicle, _ = inputs; api = API(native_map, vehicle)
    recipe.author(args, api, 'demo'); args.side = 'B'
    receipt = recipe.run(args, api, 'demo')
    assert not receipt['complete'] and len(api.jobs) == 1 and receipt['trials'][0]['side'] == 'B'
    args.side = 'all'; receipt = recipe.run(args, api, 'demo')
    assert receipt['complete'] and len(api.jobs) == 2
    api.calls.clear(); resumed = recipe.run(args, api, 'demo')
    assert resumed['trials'] == receipt['trials'] and len(api.jobs) == 2
    assert len(api.calls) == 2 and all(body is None for _, body, _ in api.calls)


def test_submission_interruption_reuses_server_idempotency_key(inputs, monkeypatch):
    args, native_map, vehicle, _ = inputs; api = API(native_map, vehicle)
    recipe.author(args, api, 'demo'); write = recipe.write
    monkeypatch.setattr(recipe, 'write', lambda *_: (_ for _ in ()).throw(OSError('authored interruption')))
    with pytest.raises(OSError): recipe.run(args, api, 'demo')
    assert len(api.jobs) == 1 and not json.loads(args.output.read_text())['trials']
    monkeypatch.setattr(recipe, 'write', write)
    assert recipe.run(args, api, 'demo')['complete'] and len(api.jobs) == 2
    keys = [key for path, _, key in api.calls if path.endswith('/executions')]
    assert keys[0] == keys[1]


def test_publish_pins_B_with_A_comparison_and_recovers_write_after_bind(inputs, monkeypatch):
    args, native_map, vehicle, _ = inputs; api = API(native_map, vehicle)
    recipe.author(args, api, 'demo'); receipt = recipe.run(args, api, 'demo')
    write = recipe.write
    monkeypatch.setattr(recipe, 'write', lambda *_: (_ for _ in ()).throw(OSError('authored interruption')))
    with pytest.raises(OSError): recipe.publish(args, api, 'demo')
    assert api.bind_count == 1
    monkeypatch.setattr(recipe, 'write', write)
    published = recipe.publish(args, api, 'demo')
    trials = {t['side']: t for t in receipt['trials']}
    assert published['published'] and api.bind_count == 1
    assert api.preset['scenario_ref'] == trials['B']['scenario_ref']
    assert api.preset['saved_execution_id'] == trials['B']['execution_id']
    assert api.preset['saved_comparison_execution_id'] == trials['A']['execution_id']
    calls = copy.deepcopy(api.calls)
    recipe.publish(args, api, 'demo'); assert api.calls == calls


def test_publish_rejects_incompatible_pair_other_binding_and_camera_free(inputs):
    args, native_map, vehicle, _ = inputs; api = API(native_map, vehicle)
    recipe.author(args, api, 'demo'); recipe.run(args, api, 'demo')
    api.compatible = False
    with pytest.raises(ValueError, match='incompatible'): recipe.publish(args, api, 'demo')
    api.compatible = True; api.preset.update(ready=True, binding_revision=2, scenario_ref={'id': 'other'})
    with pytest.raises(ValueError, match='preset preserved'): recipe.publish(args, api, 'demo')
    assert api.bind_count == 0
    receipt = json.loads(args.output.read_text()); receipt['recipe']['camera_free'] = True; recipe.write(args.output, receipt)
    with pytest.raises(ValueError, match='retained-camera'): recipe.publish(args, api, 'demo')
