"""Versioned RTL API/SDK contracts, including a real native worker execution."""
import copy
import hashlib
import json

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from ade2e.adapters import Registry
from ade2e.database import Database, Settings
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.rtl_lab import register_rtl_lab_routes, templates_payload
from ade2e.rtl_lab_schemas import RtlConfig, ADAPTER
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow as Row
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once
from axiom_rtl_lab import RtlHandler
from axiom_sdk import AxiomClient

BASE = '/api/v1/hil-sim/rtl-lab'


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='test', team_mode=True, seed_examples=False)
    db = Database(settings)
    db.initialize()
    registry = ExperimentRegistry([RtlHandler(db)], load_plugins=False)
    def auth(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    register_team_routes(router, db)
    register_rtl_lab_routes(router, db, registry)
    register_experiment_routes(router, db, registry)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        sdk = AxiomClient('http://testserver', token='test', transport=client._transport)
        yield db, client, registry, sdk


def author(env, preset=1, **changes):
    p = copy.deepcopy(templates_payload()['items'][preset])
    p['config'].update(words=512, **changes)
    sdk = env[3]
    saved = sdk.save_rtl_lab_test(name=p['name'], config=p['config'], seed=p['seed'], idempotency_key='test')
    return saved, p


def action(t):
    return {k: t[k] for k in ('project_id', 'revision', 'sha256')}


@pytest.mark.parametrize('changes', [
    {'depth': 1}, {'depth': 1025}, {'words': 16385}, {'max_cycles': 100001},
    {'resets': []}, {'resets': [1]}, {'resets': [0, 0]}, {'resets': [0, 20000]},
    {'resets': [0, True]}, {'data_width': 64},
    {'stalls': [{'start_cycle': 5, 'end_cycle': 5}]},
    {'stalls': [{'start_cycle': 5, 'end_cycle': 20}, {'start_cycle': 19, 'end_cycle': 30}]},
    {'stalls': [{'start_cycle': 5, 'end_cycle': 20001}]},
])
def test_bounded_and_unambiguous_schedule(changes):
    with pytest.raises(ValueError):
        RtlConfig.model_validate(changes)


def test_templates_saved_revisions_readback_and_idempotency(env):
    _, client, _, sdk = env
    items = sdk.rtl_lab_templates()['items']
    assert items == templates_payload()['items']
    assert len(items) == 4
    assert sdk.rtl_lab_readiness()['worker_readiness'] == 'not_verified'
    t, p = author(env)
    same = sdk.save_rtl_lab_test(name=p['name'], config=p['config'], seed=42, idempotency_key='test')
    assert t == same
    assert sdk.rtl_lab_test(t['id'], revision=1) == t
    assert sdk.rtl_lab_tests()['items'] == [t]
    changed = dict(project_id='default', id=t['id'], expected_revision=0, name='stale', config=p['config'])
    assert client.post(BASE + '/tests', json=changed, headers={'Idempotency-Key': 'stale'}).status_code == 409
    changed.update(expected_revision=1)
    assert client.post(BASE + '/tests', json=changed, headers={'Idempotency-Key': 'test'}).status_code == 409
    second = client.post(BASE + '/tests', json=changed, headers={'Idempotency-Key': 'next'})
    assert second.status_code == 201, second.text
    assert second.json()['revision'] == 2
    assert sdk.rtl_lab_test(t['id'], revision=1) == t
    assert client.get(BASE + '/tests/' + t['id'], params={'revision': 1, 'project_id': 'other'}).status_code in [403, 404]


def test_actual_worker_sdk_artifacts_and_frozen_snapshot(env, tmp_path):
    db, client, registry, sdk = env
    ready = sdk.rtl_lab_readiness()
    if not ready.get('available'):
        pytest.skip('Actual Verilator/compiler unavailable; API native execution not verified')
    t, _ = author(env)
    preview = sdk.preview_rtl_lab_test(t)
    assert sdk.preflight_rtl_lab_test(t) == preview
    assert preview['experiment']['kind'] == 'fpga_rtl'
    assert preview['experiment']['input']['test_ref']['sha256'] == t['sha256']
    job = sdk.execute_rtl_lab_test(t, idempotency_key='native')
    assert job['status'] == 'queued'
    assert sdk.execute_rtl_lab_test(t, idempotency_key='native')['id'] == job['id']
    run_once(db, Registry(load_plugins=False), 'rtl-native-test', job_id=job['id'], experiment_registry=registry)
    result = sdk.experiment_execution(job['id'])
    assert result['status'] == 'completed', result
    assert result['summary']['verdict'] == 'passed'
    assert result['summary']['output_transfers'] == 512
    assert result['summary']['input_stall_cycles'] > 0
    assert result['summary']['matched'] is True
    documents = {}
    for artifact in result['artifacts']:
        data = client.get(artifact['download_url']).content
        assert hashlib.sha256(data).hexdigest() == artifact['sha256']
        if artifact['media_type'] == 'application/json':
            documents[artifact['name']] = json.loads(data)
    assert documents['rtl-trace.json']['engine'] == 'verilator'
    assert documents['simpy-trace.json']['engine'] == 'simpy'
    assert documents['comparison.json']['matched']
    assert any(a['name'] == 'fifo.vcd' for a in result['artifacts'])
    empty_log = next(a for a in result['artifacts'] if a['name'] == 'run.log')
    assert empty_log['size_bytes'] == 0
    downloaded_log = sdk.download_experiment_artifact(job['id'], empty_log['id'], tmp_path / 'downloaded-run.log')
    assert downloaded_log.read_bytes() == b''
    assert hashlib.sha256(downloaded_log.read_bytes()).hexdigest() == empty_log['sha256']
    assert sdk.rtl_lab_executions()['items'][0]['test_ref']['sha256'] == t['sha256']
    assert sdk.export_rtl_lab_execution(job['id'])['execution']['id'] == job['id']
    changed = copy.deepcopy(preview['experiment'])
    changed['seed'] += 1
    r = client.post('/api/v1/experiments', json=changed, headers={'Idempotency-Key': 'bad-seed'})
    assert r.status_code == 422


def test_unavailable_engine_fails_closed_and_authoring_still_works(env, monkeypatch):
    _, client, registry, sdk = env
    t, _ = author(env)
    registry._handlers.pop(ADAPTER)
    ready = sdk.rtl_lab_readiness()
    assert not ready['adapter_installed'] and not ready['available']
    assert client.post(BASE + '/tests/' + t['id'] + '/preview', json=action(t)).status_code == 422
    assert sdk.rtl_lab_test(t['id'], revision=1) == t
    handler = RtlHandler(env[0])
    registry.register(handler)
    monkeypatch.setattr('axiom_rtl_lab.shutil.which', lambda _: None)
    assert sdk.rtl_lab_readiness()['available'] is False
    r = client.post(BASE + '/tests/' + t['id'] + '/preview', json=action(t))
    assert r.status_code == 422
    assert sdk.rtl_lab_executions()['items'] == []


def test_reference_hash_and_stored_revision_tamper_rejected(env):
    db, client, _, _ = env
    t, _ = author(env)
    bad = action(t) | {'sha256': '0' * 64}
    assert client.post(BASE + '/tests/' + t['id'] + '/preview', json=bad).status_code == 409
    with db.session() as session:
        row = session.get(Row, ('default', 'rtl-test', t['id'], 1))
        value = json.loads(row.manifest)
        value['config']['depth'] = 3
        row.manifest = json.dumps(value).encode()
        session.commit()
    assert client.get(BASE + '/tests/' + t['id'], params={'revision': 1}).status_code == 409
    assert client.post(BASE + '/tests/' + t['id'] + '/preview', json=action(t)).status_code == 409


def test_queued_cancel_preserves_test_and_never_runs_native(env):
    _, client, _, sdk = env
    if not sdk.rtl_lab_readiness().get('available'):
        pytest.skip('Verilator/compiler unavailable for prepared cancellation test')
    t, _ = author(env)
    job = sdk.execute_rtl_lab_test(t, idempotency_key='cancel')
    r = client.post('/api/v1/experiments/executions/' + job['id'] + '/cancel')
    assert r.status_code == 200, r.text
    result = sdk.experiment_execution(job['id'])
    assert result['status'] == 'cancelled'
    assert result['artifacts'] == []
    assert sdk.rtl_lab_test(t['id'], revision=1) == t
