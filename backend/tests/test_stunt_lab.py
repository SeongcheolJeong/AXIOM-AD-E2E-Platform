"""Stunt authoring/queue contracts with a mock handler; no native physics claims."""
import json

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient

from ade2e.adapters import Registry
from ade2e import jobs
from ade2e.database import Database, Settings
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentExecution, ExperimentHandlerMetadata, ExperimentRegistry
from ade2e.service import canonical_bytes, digest
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow as Row
from ade2e.stunt_lab import KIND, register_stunt_lab_routes, resolve_design
from ade2e.stunt_lab_schemas import ADAPTER, FIDELITY, PHYSICAL_VALIDATION, StuntConfig, model_definition
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once

BASE = '/api/v1/object-sim/stunt-lab'


class MockStuntHandler:
    """Contract fixture only: deliberately performs no force or torque integration."""
    def __init__(self, db):
        self.db = db
        self.ready = dict(available=True, physics_verified=False, reason='Mock has no native physics proof')

    def metadata(self):
        return ExperimentHandlerMetadata(id=ADAPTER, version='mock-contract/1', kinds=['object_sim'],
            status='available', fidelity=FIDELITY, resource_id='mock-stunt', cancellation_supported=True)

    def readiness(self):
        return self.ready

    def prepare(self, spec):
        if set(spec.input) != {'design_ref'}:
            raise ValueError('Only design_ref is accepted')
        with self.db.read_session() as session:
            design = resolve_design(session, spec.project_id, spec.input['design_ref'])
        if design['config']['preset'] == 'ramp':
            raise ValueError('Ramp unavailable')
        return dict(design=design, execution_fidelity=FIDELITY)

    def execute(self, snapshot, work_dir, should_cancel):
        return ExperimentExecution(summary=dict(contract_test_only=True, physical_validation=PHYSICAL_VALIDATION,
            design_ref={key: snapshot['design'][key] for key in ('id', 'revision', 'sha256')}), fidelity=FIDELITY)


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='test', team_mode=True, seed_examples=False)
    db = Database(settings)
    db.initialize()
    registry = ExperimentRegistry([MockStuntHandler(db)], load_plugins=False)

    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)

    router = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_team_routes(router, db)
    register_stunt_lab_routes(router, db, registry)
    register_experiment_routes(router, db, registry)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        yield db, client, registry


def save(client, body=None, key='save'):
    response = client.post(BASE + '/designs', json=body or dict(name='Kickflip', config=StuntConfig().model_dump()),
                           headers={'Idempotency-Key': key})
    assert response.status_code == 201, response.text
    return response.json()


def preview(client, design):
    return client.post(BASE + '/designs/' + design['id'] + '/preview',
                       json={key: design[key] for key in ('project_id', 'revision', 'sha256')})


def queue(client, design, key='run'):
    value = preview(client, design)
    assert value.status_code == 200, value.text
    value = value.json()
    created = client.post('/api/v1/experiments', json=value['experiment'], headers={'Idempotency-Key': key})
    assert created.status_code == 201, created.text
    path = '/api/v1/experiments/' + created.json()['id'] + '/executions'
    response = client.post(path, json={'preview_hash': value['preview_hash']}, headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    assert client.post(path, json={'preview_hash': value['preview_hash']}, headers={'Idempotency-Key': key}).json()['id'] == response.json()['id']
    return response.json()['id']


@pytest.mark.parametrize('changes', [
    {'mass_kg': 0}, {'mass_kg': True}, {'initial_speed_mps': -1}, {'max_thrust_n': 0},
    {'impulse_budget_ns': -1}, {'thrust_scale': 1.1}, {'thrust_scale': '0.3'}, {'target_height_m': 16},
    {'duration_s': 21}, {'duration_s': 3.003}, {'duration_s': 3.01}, {'duration_s': 3.025},
    {'dt_s': 0.1}, {'dt_s': True}, {'camera_fps': 21}, {'suction_force_n': -1},
    {'mass_kg': float('nan')}, {'max_thrust_n': float('inf')}, {'preset': 'teleport'},
    {'vehicle_blueprint': '../vehicle'}, {'map_name': '/Game/Unsafe'}, {'unknown': 1},
])
def test_strict_bounded_config(changes):
    with pytest.raises(ValueError):
        StuntConfig(**changes)


def test_model_geometry_is_versioned_and_hashed():
    value = model_definition()
    assert value.pop('sha256') == digest(value)
    assert value['version'] == 'stunt-thrusters/1'
    assert sum(thruster['max_thrust_fraction'] for thruster in value['thrusters']) == 1
    assert {tuple(t['position_m']) for t in value['thrusters']} == {
        (1.5, -.75, 0), (1.5, .75, 0), (-1.5, -.75, 0), (-1.5, .75, 0)}
    assert value['physical_validation'] == 'not_established'


@pytest.mark.parametrize('duration', [3., 3.05, 7.35, 19.95, 20.])
@pytest.mark.parametrize('dt', [.01, .005])
def test_capture_duration_fits_both_native_clocks(duration, dt):
    config = StuntConfig(duration_s=duration, dt_s=dt)
    assert duration/dt == pytest.approx(round(duration/dt))
    assert config.duration_s*config.camera_fps == pytest.approx(round(duration*config.camera_fps))


def test_misaligned_duration_rejected_before_revision_save(env):
    response = env[1].post(BASE+'/designs', json=dict(name='Invalid capture length', config={'duration_s': 3.003}),
                           headers={'Idempotency-Key': 'unaligned'})
    assert response.status_code == 422
    assert env[1].get(BASE+'/designs').json()['items'] == []


def test_immutable_revisions_idempotency_and_frozen_queue(env):
    db, client, registry = env
    first = save(client)
    assert save(client) == first
    assert first['model_definition'] == model_definition()
    second_body = dict(id=first['id'], expected_revision=1, name='Low thrust', config={'thrust_scale': .3})
    second = save(client, second_body, key='second')
    assert second['revision'] == 2
    assert client.get(BASE + '/designs/' + first['id'], params={'revision': 1}).json() == first
    assert client.post(BASE + '/designs', json=second_body, headers={'Idempotency-Key': 'stale'}).status_code == 409
    assert client.post(BASE + '/designs', json=dict(name='Other'), headers={'Idempotency-Key': 'save'}).status_code == 409
    identity = queue(client, first)
    assert len(client.get(BASE + '/executions').json()['items']) == 1
    run_once(db, Registry(), 'mock-stunt-contract', job_id=identity, experiment_registry=registry)
    run = client.get('/api/v1/experiments/executions/' + identity).json()
    assert run['status'] == 'completed', run
    assert run['summary']['contract_test_only'] is True
    assert run['summary']['design_ref']['sha256'] == first['sha256']
    assert run['adapter_id'] == ADAPTER and run['fidelity'] == FIDELITY
    receipt = client.get('/api/v1/experiments/executions/' + identity + '/export').json()
    assert receipt['prepared']['input']['design'] == first
    assert receipt['prepared']['spec']['kind'] == 'object_sim'


def test_native_readiness_is_distinct_from_installation_and_configuration(env):
    _, client, registry = env
    value = client.get(BASE + '/readiness').json()
    assert value['adapter_installed'] is True and value['configured'] is True
    assert value['available'] is False and value['physics_verified'] is False
    assert value['worker_readiness'] == 'not_verified'
    registry.get(ADAPTER).ready = {'available': True, 'physics_verified': True}
    value = client.get(BASE + '/readiness').json()
    assert value['available'] is True and value['presets']['ramp']['available'] is False
    registry.get(ADAPTER).ready = {'available': False, 'physics_verified': True, 'reason': 'Engine offline'}
    assert client.get(BASE + '/readiness').json()['available'] is False
    registry.get(ADAPTER).ready = None
    assert client.get(BASE + '/readiness').json()['details']['status'] == 'probe_failed'


def test_missing_adapter_keeps_authoring_and_ramp_fails_closed(env):
    _, client, registry = env
    ramp = save(client, dict(name='Ramp authoring', config={'preset': 'ramp'}))
    assert preview(client, ramp).status_code == 422
    saved = save(client, dict(name='Kickflip offline'), key='offline')
    registry._handlers.clear()
    assert client.get(BASE + '/templates').json()['adapter_installed'] is False
    assert client.get(BASE + '/readiness').json()['available'] is False
    assert preview(client, saved).status_code == 422
    assert client.get(BASE + '/designs/' + saved['id'], params={'revision': 1}).json() == saved


def test_invalid_revision_reference_and_tampered_content(env):
    db, client, _ = env
    saved = save(client)
    bad = saved | {'sha256': '0' * 64}
    assert preview(client, bad).status_code == 409
    with jobs._write(db) as session:
        row = session.get(Row, ('default', KIND, saved['id'], 1))
        value = json.loads(row.manifest)
        value['config']['max_thrust_n'] *= 2
        row.manifest = canonical_bytes(value)
    assert client.get(BASE + '/designs/' + saved['id'], params={'revision': 1}).status_code == 409


def test_corrupt_write_receipt_returns_conflict(env):
    db, client, _ = env
    save(client)
    with jobs._write(db) as session:
        row = session.get(Row, ('default', 'stunt-write', digest('save'), 1))
        row.manifest = b'not JSON'
    assert client.post(BASE + '/designs', json=dict(name='Kickflip', config=StuntConfig().model_dump()),
                       headers={'Idempotency-Key': 'save'}).status_code == 409


def test_project_isolation_roles_and_comparison(env):
    _, client, _ = env
    saved = save(client)
    identity = queue(client, saved)
    other = client.post('/api/v1/projects', json={'name': 'Other stunt project'}).json()['id']
    other_design = save(client, dict(project_id=other, name='Other'), key='save')
    other_run = queue(client, other_design)
    token = client.post('/api/v1/projects/default/tokens', json={'user_label': 'viewer', 'role': 'Viewer'}).json()['token']
    headers = {'Authorization': 'Bearer ' + token}
    assert client.get(BASE + '/designs', headers=headers).status_code == 200
    for path in ('/templates', '/readiness', '/designs', '/executions'):
        assert client.get(BASE + path, params={'project_id': other}, headers=headers).status_code == 404
    assert client.get(BASE + '/designs/' + other_design['id'], params={'revision': 1}, headers=headers).status_code == 404
    assert client.post(BASE + '/designs', json={'name': 'Denied'}, headers=headers | {'Idempotency-Key': 'denied'}).status_code == 403
    assert client.post(BASE + '/designs/' + saved['id'] + '/preview',
                       json={key: saved[key] for key in ('project_id', 'revision', 'sha256')}, headers=headers).status_code == 403
    own = client.get(BASE + '/executions').json()['items']
    assert [item['id'] for item in own] == [identity]
    params = dict(left_execution_id=identity, right_execution_id=other_run)
    assert client.get(BASE + '/comparison', params=params).status_code == 404
    params['right_execution_id'] = identity
    compared = client.get(BASE + '/comparison', params=params).json()
    assert compared['left']['id'] == compared['right']['id'] == identity
    assert compared['evaluation_status'] == 'not_evaluated'


def test_queue_cancellation_and_sdk_uses_same_contract(env):
    from axiom_sdk import AxiomClient
    db, client, registry = env
    sdk = AxiomClient('http://testserver', token='test', transport=client._transport)
    saved = sdk.save_stunt_lab_design(name='SDK kickflip', config={}, idempotency_key='sdk-design')
    assert sdk.stunt_lab_design(saved['id'], revision=1) == saved
    assert len(sdk.stunt_lab_templates()['items']) == 3
    assert sdk.stunt_lab_readiness()['available'] is False
    assert sdk.stunt_lab_designs()['items'][0] == saved
    submitted = sdk.execute_stunt_lab_design(saved, idempotency_key='sdk-run')
    cancelled = client.post('/api/v1/experiments/executions/' + submitted['id'] + '/cancel')
    assert cancelled.status_code == 200
    assert sdk.export_stunt_lab_execution(submitted['id'])['execution']['status'] == 'cancelled'
    with pytest.raises(ValueError):
        sdk.export_stunt_lab_execution(submitted['id'], project_id='another')
    assert sdk.stunt_lab_executions()['items'][0]['id'] == submitted['id']
    assert sdk.stunt_lab_comparison(submitted['id'], submitted['id'])['left']['id'] == submitted['id']
