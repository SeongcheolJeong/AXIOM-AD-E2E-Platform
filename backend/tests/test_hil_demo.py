"""Authored handler fixtures validate API policy; these do not run CARLA or HIL."""
import copy
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from ade2e.adapters import Registry
from ade2e.agent_catalog import catalog
from ade2e.database import Database, Settings
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentOutputFile, ExperimentRegistry
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.experiment_verification import register_experiment_verification_routes
from ade2e.experiment_verification_schemas import ExperimentVerificationCreate
from ade2e.hil_demo import ADAPTER, FIDELITY, KIND, PRESETS, register_hil_demo_routes
from ade2e.scenario_v2 import get_scenario, register_scenario_v2_routes
from ade2e.simulation_catalog import register_simulation_catalog_routes
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once
from test_scenario_city import pose, map_definition, asset_definition, reference


class SoftwareDutContractFixture:
    """Authored native-shaped envelope to test authoring/queue policy offline."""
    version = 'fixture-1'
    probes = 0
    source_revision = 1
    fail = False
    incomplete = False

    def __init__(self, db):
        self.db = db

    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER, version=self.version, kinds=['hil_demo'],
            status='available', fidelity=FIDELITY, cancellation_supported=True)

    def prepare(self, spec):
        self.probes += 1
        with self.db.read_session() as session:
            source = get_scenario(session, spec.input['scenario_id'], spec.input['scenario_revision'])
        return dict(schema_version='carla-software-dut-experiment/1', comparison_kind=spec.comparison_kind,
            fault=spec.input['fault'], fixture_source_revision=self.source_revision,
            scenario_source=dict(scenario_id=source['id'], scenario_revision=source['revision'],
                configuration_hash=source['configuration_hash'], scenario_manifest_hash=source['manifest_hash'], seed=spec.seed))

    def execute(self, snapshot, work_dir, should_cancel):
        if self.fail:
            raise RuntimeError('Authored test failure; no native engine was run')
        files = []
        for side in ('baseline', 'faulted'):
            for suffix in ('city-scene.json', 'hil-trace.json'):
                if self.incomplete and side == 'faulted' and suffix == 'hil-trace.json':
                    continue
                path = work_dir / f'{side}-{suffix}'
                path.write_text('Authored fixture only; not a CARLA or HIL result.')
                files.append(ExperimentOutputFile(path, path.name))
        return ExperimentExecution(summary=dict(schema_version='hil-demo-result/1', execution_target='software_dut',
            fault=snapshot['fault'], sides={side: dict(schema_version='carla-city-result/1',
                execution_status='completed', cleanup_status='confirmed') for side in ('baseline', 'faulted')}),
            artifacts=files, fidelity=FIDELITY)


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='test-bootstrap', team_mode=True, seed_examples=False)
    db = Database(settings)
    db.initialize()
    handler = SoftwareDutContractFixture(db)
    registry = ExperimentRegistry([handler], load_plugins=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_team_routes(api, db)
    register_simulation_catalog_routes(api, db)
    register_scenario_v2_routes(api, db)
    register_hil_demo_routes(api, db, registry)
    register_experiment_routes(api, db, registry)
    register_experiment_verification_routes(api, db)
    app = FastAPI()
    @api.get('/agent/tools')
    def tools(request: Request):
        return catalog(app, request, [], '')
    app.include_router(api)
    client = TestClient(app, headers={'Authorization': 'Bearer test-bootstrap'})
    def create_catalog(kind, identity, definition):
        response = client.post('/api/v1/' + kind, json=dict(id=identity, definition=definition))
        assert response.status_code == 201, response.text
        return reference(response.json())
    map_ref = create_catalog('maps', 'hil-map-fixture', map_definition())
    asset_ref = create_catalog('assets', 'hil-car-fixture', asset_definition())
    scenario = dict(schema_version='scenario-v2/3', name='Authored HIL API fixture', map=map_ref,
        actors=[dict(id='ego', role='ego', asset=asset_ref, pose=pose(0), route=[pose(0), pose(20)],
                     controller=dict(kind='reactive'), speed_mps=3)])
    response = client.post('/api/v1/scenario-v2', json=dict(scenario=scenario), headers={'Idempotency-Key': 'hil-scene'})
    assert response.status_code == 201, response.text
    ref = {key: response.json()[key] for key in ('id', 'revision', 'configuration_hash', 'manifest_hash')}
    yield db, client, handler, registry, ref
    db.engine.dispose()


def bind(env, identity='input_delay', **changes):
    return env[1].post(f'/api/v1/hil-sim/presets/{identity}/bindings', json=dict(scenario_ref=env[4], **changes))


def preview(env, identity='input_delay', **changes):
    return env[1].post(f'/api/v1/hil-sim/presets/{identity}/preview', json=dict(expected_binding_revision=1, **changes))


def execute(env, identity='input_delay', key='execute', complete=True, parameters=None):
    db, client, _, registry, _ = env
    document = preview(env, identity, parameters=parameters or {}).json()
    created = client.post('/api/v1/experiments', json=document['experiment'], headers={'Idempotency-Key': key})
    assert created.status_code == 201, created.text
    response = client.post('/api/v1/experiments/' + created.json()['id'] + '/executions',
        json={'preview_hash': document['preview']['preview_hash']}, headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    job = response.json()['id']
    if complete:
        run_once(db, Registry(), 'hil-authored-fixture', job_id=job, experiment_registry=registry)
    return job


def test_binding_is_explicit_idempotent_and_does_not_probe_or_pick_saved_results(env):
    _, client, handler, _, _ = env
    items = client.get('/api/v1/hil-sim/presets').json()['items']
    assert [item['id'] for item in items] == list(PRESETS)
    assert all(item['status'] == 'unbound' for item in items)
    assert all(item['parameters'][0]['default'] == item['defaults'][item['parameters'][0]['key']] for item in items)
    assert items[0]['fault_timing'] == dict(schema_version='hil-fault-timing/1', start_min_s=1, start_max_s=17,
        end_max_s=17.5, duration_min_s=.5, duration_max_s=4, step_s=.5, default_start_s=3, default_duration_s=4)
    assert items[1]['fault_timing']['default_start_s'] == 3.5
    assert items[1]['fault_timing']['default_duration_s'] == 1
    first = bind(env)
    assert first.status_code == 200, first.text
    assert first.json()['revision'] == 1
    assert bind(env).json() == first.json()
    assert bind(env, seed=43).status_code == 409
    assert bind(env, seed=43, expected_revision=1).json()['revision'] == 2
    assert preview(env).status_code == 409
    item = client.get('/api/v1/hil-sim/presets').json()['items'][0]
    assert item['ready'] and item['saved_execution_id'] is None
    assert item['source']['seed'] == 43 and handler.probes == 0


@pytest.mark.parametrize('identity,parameters,fault', [
    ('input_delay', {}, dict(kind='delay', start_s=3, duration_s=4, delay_ms=300)),
    ('input_delay', {'delay_ms': 1000}, dict(kind='delay', start_s=3, duration_s=4, delay_ms=1000)),
    ('input_dropout', {'duration_s': 1.5}, dict(kind='drop', start_s=3.5, duration_s=1.5, delay_ms=0)),
    ('input_delay', {'delay_ms': 300, 'start_s': 5.5, 'duration_s': 2}, dict(kind='delay', start_s=5.5, duration_s=2, delay_ms=300)),
    ('input_delay', {'start_s': 1.0}, dict(kind='delay', start_s=1, duration_s=4, delay_ms=300)),
    ('input_delay', {'duration_s': .5}, dict(kind='delay', start_s=3, duration_s=.5, delay_ms=300)),
    ('input_dropout', {'start_s': 17, 'duration_s': .5}, dict(kind='drop', start_s=17, duration_s=.5, delay_ms=0)),
])
def test_preview_preserves_source_and_matches_normal_experiment_preview(env, identity, parameters, fault):
    assert bind(env, identity).status_code == 200
    response = preview(env, identity, parameters=parameters)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document['schema_version'] == 'hil-sim-preset-preview/1'
    assert document['experiment']['kind'] == 'hil_demo'
    assert document['experiment']['comparison_kind'] == 'same_scene_fault_ab'
    assert document['experiment']['input'] == dict(scenario_id=env[4]['id'], scenario_revision=1, fault=fault)
    assert document['parameters'] == {PRESETS[identity]['key']: PRESETS[identity]['default'], **parameters}
    assert {item['key']: item['value'] for item in document['changes']} == document['parameters']
    assert document['preview'] == env[1].post('/api/v1/experiments/preview', json=document['experiment']).json()


@pytest.mark.parametrize('identity,parameters', [
    ('input_delay', {'delay_ms': True}), ('input_delay', {'delay_ms': '300'}),
    ('input_delay', {'delay_ms': 49}), ('input_delay', {'delay_ms': 75}),
    ('input_delay', {'delay_ms': 1050}), ('input_delay', {'end_s': 8}),
    ('input_dropout', {'duration_s': .25}), ('input_dropout', {'duration_s': 4.5}),
    ('input_delay', {'start_s': True}), ('input_delay', {'start_s': '3'}),
    ('input_delay', {'start_s': .5}), ('input_delay', {'start_s': 17.5}),
    ('input_delay', {'start_s': 1.25}), ('input_delay', {'duration_s': 2.25}),
    ('input_delay', {'start_s': 14}), ('input_dropout', {'start_s': 17}),
    ('input_dropout', {'start_s': 14, 'duration_s': 4}),
    ('input_delay', {'start_s': 16.00000001, 'duration_s': .5}),
    ('input_delay', {'start_s': 3.000000001}),
    ('input_delay', {'duration_s': 1.0000000001}),
    ('input_dropout', {'duration_s': 1.0000000001}),
])
def test_unsupported_edits_fail_before_native_probe(env, identity, parameters):
    bind(env, identity)
    assert preview(env, identity, parameters=parameters).status_code == 422
    assert env[2].probes == 0


@pytest.mark.parametrize('field,value', [('start_s', float('nan')), ('duration_s', float('inf')),
                                      ('start_s', -float('inf'))])
def test_nonfinite_timing_rejected_by_normalizer(field, value):
    from fastapi import HTTPException
    from ade2e.hil_demo import _parameters
    with pytest.raises(HTTPException) as caught:
        _parameters('input_delay', {field: value})
    assert caught.value.status_code == 422


def test_native_drift_does_not_submit_reviewed_old_preview(env):
    bind(env)
    draft = preview(env).json()
    env[2].source_revision += 1
    created = env[1].post('/api/v1/experiments', json=draft['experiment'], headers={'Idempotency-Key': 'drift'})
    assert created.status_code == 201
    assert created.json()['preview_hash'] != draft['preview']['preview_hash']
    rejected = env[1].post('/api/v1/experiments/' + created.json()['id'] + '/executions',
        json={'preview_hash': draft['preview']['preview_hash']}, headers={'Idempotency-Key': 'drift'})
    assert rejected.status_code == 409
    env[2].version = 'fixture-2'
    assert preview(env).status_code == 422
    assert env[1].get('/api/v1/hil-sim/presets').json()['items'][0]['status'] == 'invalid'


@pytest.mark.parametrize('identity', list(PRESETS))
def test_saved_default_completed_result_must_be_explicit_and_matches_only_its_preset(env, identity):
    bind(env, identity)
    job = execute(env, identity)
    items = env[1].get('/api/v1/hil-sim/presets').json()['items']
    assert all(item['saved_execution_id'] is None for item in items)
    attached = bind(env, identity, expected_revision=1, saved_execution_id=job)
    assert attached.status_code == 200, attached.text
    item = next(item for item in env[1].get('/api/v1/hil-sim/presets').json()['items'] if item['id'] == identity)
    assert item['ready'] and item['saved_execution_id'] == job
    other = 'input_dropout' if identity == 'input_delay' else 'input_delay'
    assert bind(env, other, saved_execution_id=job).status_code == 422


@pytest.mark.parametrize('mode', ['pending', 'failed', 'incomplete', 'nondefault'])
def test_ineligible_saved_result_rejected(env, mode):
    bind(env)
    env[2].fail = mode == 'failed'
    env[2].incomplete = mode == 'incomplete'
    job = execute(env, complete=mode != 'pending', parameters={'delay_ms': 500} if mode == 'nondefault' else {})
    assert bind(env, expected_revision=1, saved_execution_id=job).status_code == 422


def test_hash_pin_tampering_fails_closed_and_other_presets_stay_listable(env):
    ref = copy.deepcopy(env[4])
    ref['configuration_hash'] = 'a' * 64
    assert env[1].post('/api/v1/hil-sim/presets/input_delay/bindings', json={'scenario_ref': ref}).status_code == 409
    bind(env)
    with env[0].session() as session:
        row = session.get(SimulationCatalogRevisionRow, ('default', KIND, 'input_delay', 1))
        row.manifest += b' '
        session.commit()
    items = env[1].get('/api/v1/hil-sim/presets').json()['items']
    assert items[0]['status'] == 'invalid' and not items[0]['ready']
    assert items[1]['status'] == 'unbound'
    assert preview(env).status_code == 409


def test_project_permissions_and_same_openapi_agent_surface(env):
    bind(env)
    client = env[1]
    project = client.post('/api/v1/projects', json={'name': 'Other'}).json()['id']
    assert bind(env, project_id=project).status_code == 404
    for role in ('Viewer', 'Reviewer', 'Engineer'):
        token = client.post('/api/v1/projects/default/tokens', json={'user_label': role, 'role': role}).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        assert client.get('/api/v1/hil-sim/presets', headers=headers).status_code == 200
        assert client.get('/api/v1/hil-sim/presets', headers=headers, params={'project_id': project}).status_code == 404
        assert client.get('/api/v1/hil-sim/scenes', headers=headers).status_code == 200
        assert client.get('/api/v1/hil-sim/scenes', headers=headers, params={'project_id': project}).status_code == 404
        expected = 200 if role == 'Engineer' else 403
        assert client.post('/api/v1/hil-sim/presets/input_delay/preview', headers=headers,
            json={'expected_binding_revision': 1}).status_code == expected
        tools = client.get('/api/v1/agent/tools', headers=headers).json()['tools']
        hil = [tool for tool in tools if '/hil-sim/' in tool['path']]
        assert len(hil) == (4 if role == 'Engineer' else 2)
        if role == 'Engineer':
            assert next(tool for tool in hil if tool['path'].endswith('/preview'))['read_only']
    assert client.get('/api/v1/hil-sim/presets', headers={'Authorization': ''}).status_code == 401


def test_hil_comparison_and_context_only_verification_contracts():
    with pytest.raises(ValidationError, match='same_scene_fault_ab'):
        ExperimentCreate(name='Invalid', kind='hil_demo', adapter_id=ADAPTER, input={})
    with pytest.raises(ValidationError, match='same_scene_fault_ab'):
        ExperimentCreate(name='Invalid', kind='object_sim', adapter_id=ADAPTER, input={}, comparison_kind='same_scene_fault_ab')
    plan = dict(name='DUT demo context', purpose='Software demonstration', experiment_kind='hil_demo', allowed_fidelities=[FIDELITY])
    assert ExperimentVerificationCreate(**plan).criteria == []
    with pytest.raises(ValidationError, match='context evidence only'):
        ExperimentVerificationCreate(**plan, criteria=[dict(metric='collision', unit='boolean', operator='eq', threshold=False)])


def test_completed_software_dut_execution_remains_context_only_in_verification(env):
    bind(env)
    job = execute(env)
    client = env[1]
    capabilities = client.get('/api/v1/experiment-verification-plans/capabilities').json()
    assert 'hil_demo' in capabilities['context_only_kinds']
    response = client.post('/api/v1/experiment-verification-plans',
        json=dict(name='Software DUT evidence', purpose='Demonstration context', experiment_kind='hil_demo', allowed_fidelities=[FIDELITY]),
        headers={'Idempotency-Key': 'hil-context'})
    assert response.status_code == 201, response.text
    result = client.post('/api/v1/experiment-verification-plans/' + response.json()['id'] + '/evaluations',
        json=dict(plan_revision=1, execution_ids=[job]), headers={'Idempotency-Key': 'hil-context-evaluation'})
    assert result.status_code == 201, result.text
    value = result.json()
    assert value['verdict'] == 'not_evaluated'
    assert value['counts']['context_only'] == 1 and value['counts']['passed'] == 0
    assert value['is_safety_validation'] is False
