"""Urban preparation/resume contracts using authored data; no native jobs or network."""
import copy
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import object_sim_urban_demo as urban


def node(identity, x, lane, next_id=None):
    return {'id': identity, 'x': x, 'y': 0 if lane == -1 else 3.5, 'z': 0,
        'heading_rad': math.pi if lane == -1 else 0, 'road_id': 12, 'section_id': 0,
        'lane_id': lane, 'is_junction': False, 'next_ids': [next_id] if next_id else []}


@pytest.fixture
def inputs(tmp_path):
    ego = [node('e' + str(i), x, -1, 'e' + str(i + 1) if i < 21 else None)
        for i, x in enumerate(range(28, -16, -2))]
    opposite = [node('o' + str(i), x, 1, 'o' + str(i + 1) if i < 40 else None)
        for i, x in enumerate(range(-40, 42, 2))]
    definition = {'native_name': 'Carla/Maps/Town10HD_Opt', 'nodes': ego + opposite,
        'opendrive': {'content': 'authored fixture'}, 'rights': {'license': 'authored contract only'}}
    native_map = {'id': 'carla-town10hd-opt', 'revision': 1, 'sha256': 'a' * 64, 'definition': definition}
    vehicle = {'id': 'prius', 'revision': 1, 'sha256': 'b' * 64,
        'definition': {'blueprint_id': 'vehicle.toyota.prius'}}
    source = {'case': 'straight', 'map': urban.ref(native_map),
        'saved_scenario': {'id': 'source', 'revision': 1, 'configuration_hash': 'c' * 64, 'manifest_hash': 'd' * 64},
        'scenario': {'actors': [{'route': [urban.pose(n) for n in ego]}]}}
    args = SimpleNamespace(catalog=tmp_path / 'catalog.json', source_suite=tmp_path / 'source.json',
        output=tmp_path / 'receipt.json', probe=False, timeout=1)
    args.catalog.write_text(json.dumps({'map': definition}))
    args.source_suite.write_text(json.dumps({'cases': [source]}))
    return args, native_map, vehicle, source


class API:
    """A small idempotent HTTP contract double, including summary-only map lists."""
    def __init__(self, native_map, vehicle, exists=True):
        self.native_map = native_map
        self.vehicle = vehicle
        self.exists = exists
        self.calls = []
        self.responses = {}
        self.jobs = {}
        self.preset = {'id': 'urban_city', 'ready': False, 'binding_revision': None,
            'scenario_ref': None, 'saved_execution_id': None}
        self.scenarios = []
        self.bind_count = 0

    def items(self, kind, project):
        assert project == 'demo'
        self.calls.append((kind, project, None))
        if kind == 'maps':
            summary = {**self.native_map, 'definition': {k: v for k, v in self.native_map['definition'].items()
                if k not in {'nodes', 'opendrive'}}}
            return [summary] if self.exists else []
        assert kind == 'assets'
        return [self.vehicle]

    def request(self, path, body=None, key=None):
        self.calls.append((path, copy.deepcopy(body), key))
        if key in self.responses:
            return copy.deepcopy(self.responses[key])
        if path.startswith('/maps/carla-town10hd-opt?'):
            assert 'project_id=demo' in path and 'revision=1' in path
            return copy.deepcopy(self.native_map)
        if path == '/maps':
            assert body['project_id'] == 'demo' and body['definition'] == self.native_map['definition']
            self.exists = True
            return copy.deepcopy(self.native_map)
        if path == '/scenario-v2/yaml/validate':
            assert body['project_id'] == 'demo'
            scenario = json.loads(body['yaml'])
            return {'scenario': scenario, 'configuration_hash': urban.digest(scenario)}
        if path == '/scenario-v2':
            assert body['project_id'] == 'demo'
            scenario = copy.deepcopy(body['scenario']); self.scenarios.append(scenario)
            result = {'id': 'scenario-' + str(len(self.scenarios)), 'revision': 1,
                'configuration_hash': urban.digest(scenario), 'manifest_hash': urban.digest({'scenario': scenario})}
        elif path == '/experiments':
            assert body['project_id'] == 'demo'
            result = {'id': 'experiment-' + str(len(self.scenarios)), 'preview_hash': 'e' * 64}
        elif path.startswith('/experiments/experiment-') and path.endswith('/executions'):
            identity = 'job-' + str(len(self.jobs) + 1)
            result = {'id': identity}
            self.jobs[identity] = {'id': identity, 'project_id': 'demo', 'status': 'completed',
                'manifest_hash': 'f' * 64, 'outcome_uncertain': False,
                'summary': {'verification_verdict': 'not_evaluated', 'camera_frame_count': 480}}
        elif path.startswith('/experiments/executions/'):
            return copy.deepcopy(self.jobs[path.rsplit('/', 1)[1]])
        elif path == '/object-sim/presets?project_id=demo':
            return {'items': [copy.deepcopy(self.preset)]}
        elif path == '/object-sim/presets/urban_city/bind':
            assert body['project_id'] == 'demo' and body['expected_revision'] == 0
            self.bind_count += 1
            self.preset.update(ready=True, binding_revision=1, scenario_ref=body['scenario_ref'],
                saved_execution_id=body['saved_execution_id'])
            return {'revision': 1}
        else:
            pytest.fail('Unexpected request ' + path)
        self.responses[key] = copy.deepcopy(result)
        return result


def test_native_graph_routes_and_exact_two_camera_capture_contract(inputs):
    _, native_map, vehicle, source = inputs
    scenario = urban.scenario_definition(native_map, vehicle, source)
    assert scenario['duration_s'] == 24 and scenario['timestep_s'] == .05
    assert scenario['map'] == urban.ref(native_map)
    nodes = native_map['definition']['nodes']
    for actor in scenario['actors']:
        chosen = [next(n for n in nodes if urban.pose(n) == p) for p in actor['route']]
        assert all(b['id'] in a['next_ids'] for a, b in zip(chosen, chosen[1:]))
        assert actor['pose'] == actor['route'][0] and actor['destination'] == actor['route'][-1]
        assert not actor['planner']['allow_lane_changes']
    assert len(scenario['actors']) == 3
    assert {s['id'] for s in scenario['sensors']} == {'front', 'chase'}
    assert all((s['width'], s['height'], s['period_s']) == (960, 540, .1) for s in scenario['sensors'])
    assert sum(round(scenario['duration_s'] / s['period_s']) for s in scenario['sensors']) == 480
    bad = copy.deepcopy(native_map); bad['definition']['generated'] = {'source': 'authored'}
    with pytest.raises(ValueError, match='native Town10'):
        urban.scenario_definition(bad, vehicle, source)
    broken = copy.deepcopy(native_map)
    next(n for n in broken['definition']['nodes'] if n['x'] == 0 and n['lane_id'] == 1)['next_ids'] = []
    with pytest.raises(ValueError, match='every native graph edge'):
        urban.scenario_definition(broken, vehicle, source)


@pytest.mark.parametrize('exists', [False, True])
def test_author_fetches_full_existing_map_and_reuses_pinned_receipt(inputs, exists):
    args, native_map, vehicle, source = inputs
    api = API(native_map, vehicle, exists)
    receipt = urban.author(args, api, 'demo')
    assert receipt['source_scenario_ref'] == source['saved_scenario']
    assert receipt['native_map_ref'] == urban.ref(native_map)
    assert receipt['configuration_hash'] == urban.digest(receipt['scenario'])
    if exists:
        assert any(path.startswith('/maps/carla-town10hd-opt?') for path, _, _ in api.calls)
        assert not any(path == '/maps' for path, _, _ in api.calls)
    calls = copy.deepcopy(api.calls)
    assert urban.author(args, api, 'demo') == receipt and api.calls == calls
    with pytest.raises(ValueError, match='another project or input'):
        urban.author(args, api, 'another-project')
    args.probe = True
    with pytest.raises(ValueError, match='another project or input'):
        urban.author(args, api, 'demo')
    assert api.calls == calls


def test_run_resume_reuses_two_jobs_and_preserves_original_scenario(inputs):
    args, native_map, vehicle, _ = inputs
    api = API(native_map, vehicle)
    original = urban.author(args, api, 'demo')
    result = urban.run(args, api, 'demo')
    assert result['complete'] and not result['published']
    assert len(api.jobs) == 2 and [s['actors'][0]['speed_mps'] for s in api.scenarios] == [4, 6]
    for scenario in api.scenarios:
        assert scenario['map'] == original['native_map_ref']
        assert scenario['actors'][0]['route'] == original['scenario']['actors'][0]['route']
        assert scenario['acceptance'] == original['scenario']['acceptance']
    assert result['scenario'] == original['scenario']
    api.calls.clear()
    resumed = urban.run(args, api, 'demo')
    assert len(api.jobs) == 2 and resumed['trials'] == result['trials']
    assert len(api.calls) == 2 and all(body is None for _, body, _ in api.calls)


def test_run_crash_after_submission_reuses_server_idempotency_keys(inputs, monkeypatch):
    args, native_map, vehicle, _ = inputs
    api = API(native_map, vehicle)
    urban.author(args, api, 'demo')
    write = urban.write
    def interrupted_write(path, receipt):
        if receipt['trials']:
            raise OSError('Authored interruption after server accepted execution')
        write(path, receipt)
    monkeypatch.setattr(urban, 'write', interrupted_write)
    with pytest.raises(OSError):
        urban.run(args, api, 'demo')
    assert len(api.jobs) == 1 and not json.loads(args.output.read_text())['trials']
    monkeypatch.setattr(urban, 'write', write)
    result = urban.run(args, api, 'demo')
    assert len(api.jobs) == 2 and len(result['trials']) == 2
    first_keys = [key for path, _, key in api.calls if path.endswith('/executions')]
    assert first_keys[0] == first_keys[1]


def test_wrong_project_or_changed_configuration_fails_before_submission(inputs):
    args, native_map, vehicle, _ = inputs
    api = API(native_map, vehicle)
    receipt = urban.author(args, api, 'demo'); api.calls.clear()
    with pytest.raises(ValueError, match='Wrong urban receipt'):
        urban.run(args, api, 'different-project')
    receipt['scenario']['actors'][0]['speed_mps'] = 12
    urban.write(args.output, receipt)
    with pytest.raises(ValueError, match='pinned hash'):
        urban.run(args, api, 'demo')
    assert not api.calls and not api.jobs


def test_publish_only_urban_and_resume_after_binding_before_receipt_write(inputs, monkeypatch):
    args, native_map, vehicle, _ = inputs
    api = API(native_map, vehicle)
    urban.author(args, api, 'demo'); urban.run(args, api, 'demo')
    write = urban.write
    monkeypatch.setattr(urban, 'write', lambda *_: (_ for _ in ()).throw(OSError('Authored interruption')))
    with pytest.raises(OSError):
        urban.publish(args, api, 'demo')
    assert api.bind_count == 1 and not json.loads(args.output.read_text())['published']
    monkeypatch.setattr(urban, 'write', write)
    result = urban.publish(args, api, 'demo')
    assert result['published'] and result['binding_revision'] == 1 and api.bind_count == 1
    calls = copy.deepcopy(api.calls)
    assert urban.publish(args, api, 'demo') == result and api.calls == calls
    assert [path for path, body, _ in api.calls if body is not None and '/bind' in path] == ['/object-sim/presets/urban_city/bind']


def test_publish_preserves_other_binding_and_rejects_probe_or_incomplete(inputs):
    args, native_map, vehicle, _ = inputs
    api = API(native_map, vehicle)
    receipt = urban.author(args, api, 'demo')
    with pytest.raises(ValueError, match='completed full'):
        urban.publish(args, api, 'demo')
    urban.run(args, api, 'demo')
    api.preset.update(ready=True, binding_revision=1, scenario_ref={'id': 'another'}, saved_execution_id='another-job')
    with pytest.raises(ValueError, match='binding preserved'):
        urban.publish(args, api, 'demo')
    assert not api.bind_count
    receipt = json.loads(args.output.read_text()); receipt['probe'] = True; urban.write(args.output, receipt)
    with pytest.raises(ValueError, match='completed full'):
        urban.publish(args, api, 'demo')
