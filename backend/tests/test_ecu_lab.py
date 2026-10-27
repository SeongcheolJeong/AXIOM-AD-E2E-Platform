"""Project/revision contracts and actual queued SimPy ECU executions."""
import copy
import json
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from ade2e.adapters import Registry
from ade2e.database import Database, Settings
from ade2e.ecu_lab import register_ecu_lab_routes, templates_payload
from ade2e.ecu_lab_schemas import EcuProfileConfig, EcuTestSave, validate_runtime
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.fpga_lab import register_fpga_lab_routes
from ade2e.fpga_lab_schemas import TimingConfig
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once
pytest.importorskip('axiom_fpga_lab', reason='Install the optional FPGA Lab adapter')
from axiom_fpga_lab import TimingHandler
from axiom_fpga_lab.ecu import EcuTimingHandler

BASE = '/api/v1/hil-sim/ecu-lab'
FPGA = '/api/v1/hil-sim/fpga-lab'
REF_KEYS = ('id', 'revision', 'sha256')
def ref(value): return {k: value[k] for k in REF_KEYS}

@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='test', team_mode=True, seed_examples=False)
    db = Database(settings); db.initialize()
    registry = ExperimentRegistry([TimingHandler(db), EcuTimingHandler(db)], load_plugins=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_team_routes(router, db)
    register_fpga_lab_routes(router, db, registry)
    register_ecu_lab_routes(router, db, registry)
    register_experiment_routes(router, db, registry)
    app = FastAPI(); app.include_router(router)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        yield db, client, registry

def post(client, path, body, key):
    r = client.post(path, json=body, headers={'Idempotency-Key': key})
    assert r.status_code == 201, r.text
    return r.json()

def authored(env, preset=None, key='sample'):
    _, client, _ = env
    if preset is None:
        preset = copy.deepcopy(templates_payload()['items'][0])
        preset['profile']['config'].update(width=8, height=2)
        preset['channels'][0]['config'].update(width=8, height=2, frames=6)
    profile = post(client, BASE+'/profiles', dict(name=preset['name'], config=preset['profile']['config']), key+'-profile')
    channels = []
    for ch in preset['channels']:
        design = post(client, FPGA+'/designs', dict(name=f"{key} CH{ch['channel']}", config=ch['config'], seed=preset['seed']), key+f"-ch{ch['channel']}")
        channels.append({k:v for k,v in ch.items() if k != 'config'} | {'design_ref': ref(design)})
    body = dict(name=preset['name'], profile_ref=ref(profile), channels=channels,
                commands=preset['commands'], faults=preset['faults'], seed=preset['seed'])
    return profile, body

def save_test(env, body, key='save-test'):
    return post(env[1], BASE+'/tests', body, key)

def execute(env, saved, key='run', complete=True):
    db, client, registry = env
    preview = client.post(BASE+'/tests/'+saved['id']+'/preview', json={k:saved[k] for k in ('project_id','revision','sha256')})
    assert preview.status_code == 200, preview.text
    preview = preview.json()
    experiment = post(client, '/api/v1/experiments', preview['experiment'], key)
    path = '/api/v1/experiments/'+experiment['id']+'/executions'
    args = dict(json={'preview_hash': preview['preview_hash']}, headers={'Idempotency-Key': key})
    submitted = client.post(path, **args)
    assert submitted.status_code == 202, submitted.text
    assert client.post(path, **args).json()['id'] == submitted.json()['id']
    identity = submitted.json()['id']
    if complete:
        run_once(db, Registry(), 'ecu-real-simpy', job_id=identity, experiment_registry=registry)
    return identity

def detail(env, identity):
    return env[1].get('/api/v1/experiments/executions/'+identity).json()

def test_profile_test_revision_hash_and_sdk_execution(env):
    from axiom_sdk import AxiomClient
    _, client, _ = env
    profile, body = authored(env)
    saved = save_test(env, body)
    assert save_test(env, body) == saved
    assert profile['sources'][0]['status'] == 'article_observed'
    assert profile['assumptions']
    p2 = post(client, BASE+'/profiles', dict(id=profile['id'],expected_revision=1,name='Slower reference',
        config=profile['config'] | {'processing_us': 20000}), 'profile2')
    assert p2['revision'] == 2
    t2 = save_test(env, body | dict(id=saved['id'],expected_revision=1,profile_ref=ref(p2)), 'test2')
    assert t2['revision'] == 2
    sdk = AxiomClient('http://testserver', token='test', transport=client._transport)
    assert sdk.ecu_lab_test(saved['id'], revision=1) == saved
    assert sdk.ecu_lab_profile(profile['id'], revision=1) == profile
    assert len(sdk.ecu_lab_templates()['items']) == 6
    submitted = sdk.execute_ecu_lab_test(saved, idempotency_key='sdk-run')
    assert sdk.execute_ecu_lab_test(saved, idempotency_key='sdk-run')['id'] == submitted['id']
    run_once(env[0], Registry(), 'ecu-sdk-real-simpy', job_id=submitted['id'], experiment_registry=env[2])
    run = detail(env, submitted['id'])
    assert run['status'] == 'completed', run
    assert run['summary']['completed'] == 6
    assert run['fidelity'] == 'behavioral_timing'
    artifact = next(a for a in run['artifacts'] if a['name'] == 'ecu-trace.json')
    trace = client.get(artifact['download_url']).json()
    assert trace['test_ref'] == ref(saved)
    assert trace['profile_ref'] == ref(profile)
    assert trace['adapter_version'] == '0.1.0'
    assert sdk.export_ecu_lab_execution(submitted['id']) == client.get('/api/v1/experiments/executions/'+submitted['id']+'/export').json()
    assert sdk.ecu_lab_executions()['items'][0]['test_ref'] == ref(saved)
    assert client.get(FPGA+'/executions').json()['items'] == []


def test_rejected_contracts_are_not_executions(env):
    _, client, _ = env
    profile, body = authored(env)
    duplicate = copy.deepcopy(body)
    duplicate['channels'].append(duplicate['channels'][0] | {'channel': 1})
    assert client.post(BASE+'/tests',json=duplicate,headers={'Idempotency-Key':'dupe'}).status_code == 422
    bad_profile = post(client, BASE+'/profiles', dict(name='RAW10',config=profile['config'] | {'bits':10}), 'raw10')
    mismatch = body | {'profile_ref':ref(bad_profile)}
    response = client.post(BASE+'/tests',json=mismatch,headers={'Idempotency-Key':'mismatch'})
    assert response.status_code == 422 and 'RAW' in response.text
    assert client.get(BASE+'/tests').json()['items'] == []
    assert client.get(BASE+'/executions').json()['items'] == []
    tamper = copy.deepcopy(body); tamper['channels'][0]['design_ref']['sha256']='0'*64
    assert client.post(BASE+'/tests',json=tamper,headers={'Idempotency-Key':'hash'}).status_code == 409
    saved = save_test(env,body)
    conflict = body | dict(id=saved['id'],expected_revision=0)
    assert client.post(BASE+'/tests',json=conflict,headers={'Idempotency-Key':'stale'}).status_code == 409
    assert client.post(BASE+'/tests',json=body | {'name':'changed'},headers={'Idempotency-Key':'save-test'}).status_code == 409
    bad_action = {k:saved[k] for k in ('project_id','revision','sha256')} | {'sha256':'0'*64}
    assert client.post(BASE+'/tests/'+saved['id']+'/preview',json=bad_action).status_code == 409


def test_access_and_cross_project_refs(env):
    _, client, _ = env
    _, body = authored(env)
    saved = save_test(env,body)
    other = client.post('/api/v1/projects',json={'name':'Other ECU lab'}).json()['id']
    assert client.post(BASE+'/tests',json=body | {'project_id':other},headers={'Idempotency-Key':'cross'}).status_code == 404
    token = client.post('/api/v1/projects/default/tokens',json={'user_label':'viewer','role':'Viewer'}).json()['token']
    headers = {'Authorization':'Bearer '+token}
    assert client.get(BASE+'/tests',headers=headers).status_code == 200
    assert client.get(BASE+'/tests',params={'project_id':other},headers=headers).status_code == 404
    assert client.post(BASE+'/profiles',json=dict(name='Forbidden',config=EcuProfileConfig().model_dump()),headers=headers | {'Idempotency-Key':'no'}).status_code == 403
    assert client.post(BASE+'/tests/'+saved['id']+'/preview',json={k:saved[k] for k in ('project_id','revision','sha256')},headers=headers).status_code == 403


def test_cancel_failure_and_offline_authoring(env, monkeypatch):
    _, client, registry = env
    _, body = authored(env)
    saved = save_test(env,body)
    job = execute(env,saved,complete=False)
    assert client.post('/api/v1/experiments/executions/'+job+'/cancel').status_code == 200
    assert detail(env,job)['status'] == 'cancelled'
    def fail(*args): raise RuntimeError('Intentional ECU adapter error for queue verification')
    monkeypatch.setattr(registry.get('sa8650-ecu-behavioral'),'execute',fail)
    job = execute(env,saved,key='fail')
    assert detail(env,job)['status'] == 'failed'
    assert not detail(env,job)['summary']
    registry._handlers.clear()
    assert not client.get(BASE+'/templates').json()['adapter_installed']
    assert client.get(BASE+'/tests/'+saved['id'],params={'revision':1}).status_code == 200
    assert client.post(BASE+'/tests/'+saved['id']+'/preview',json={k:saved[k] for k in ('project_id','revision','sha256')}).status_code == 422


@pytest.mark.parametrize('preset',templates_payload()['items'],ids=lambda p:p['id'])
def test_full_resolution_presets_through_actual_queue(env,preset):
    _, client, _ = env
    _, body = authored(env,preset,key=preset['id'])
    if preset['expected_outcome'] == 'preflight_rejected':
        response = client.post(BASE+'/tests',json=body,headers={'Idempotency-Key':'expected-rejection'})
        assert response.status_code == 422
        assert client.get(BASE+'/executions').json()['items'] == []
        return
    job = execute(env,save_test(env,body))
    run = detail(env,job)
    assert run['status'] == 'completed', run
    s = run['summary']
    if preset['id'] == 'normal':
        assert s['completed'] == 60 and s['dropped'] == 0
    elif preset['id'] == 'before-ready':
        assert s['dropped'] > 0 and s['completed'] > 0
    elif preset['id'] == 'link-recovery':
        assert s['dropped'] > 0 and s['completed'] > 0
        channels = {c['channel']:c for c in s['channels']}
        assert channels[1]['completed'] == 30
        assert channels[0]['dropped'] > 0
    elif preset['id'] == 'csi-congestion':
        assert s['deadline_exceeded'] > 0
    elif preset['id'] == 'processor-backlog':
        assert s['dropped'] > 0 and s['deadline_exceeded'] > 0


def test_aggregate_budget_and_runtime_bound():
    config = TimingConfig(width=8,height=1,frames=120).model_dump()
    channel = dict(channel=0,vc=0,source_offset_us=120000,duplicate_every=0,design={'config':config})
    with pytest.raises(ValueError,match='120 frames'):
        validate_runtime(EcuProfileConfig(width=8,height=1).model_dump(),[channel,channel | {'channel':1,'vc':1}],[],[])
    with pytest.raises(ValueError,match='event budget'):
        large = channel | {'design': {'config': config | {'height':1200}}}
        validate_runtime(EcuProfileConfig(width=8,height=1200).model_dump(),[large],[],[])
    with pytest.raises(ValueError,match='60 virtual seconds'):
        validate_runtime(EcuProfileConfig(width=8,height=1,processing_us=1000000).model_dump(),[channel],[],[])
