"""Actual SimPy CPU executions, independent timing oracles and API isolation."""
import copy
import json
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from ade2e.adapters import Registry, AdapterCancelledBeforeDispatch
from ade2e.database import Database, Settings
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.fpga_lab import register_fpga_lab_routes
from ade2e.fpga_lab_schemas import TimingConfig, PRESETS
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once
pytest.importorskip('axiom_fpga_lab', reason='Install the optional FPGA Lab adapter to run actual SimPy tests')
from axiom_fpga_lab import TimingHandler, simulate

BASE = '/api/v1/hil-sim/fpga-lab'

def small(**changes):
    return TimingConfig(width=8, height=2, frames=3, preload_frames=1, **changes).model_dump()

@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='test', team_mode=True, seed_examples=False)
    db = Database(settings); db.initialize()
    registry = ExperimentRegistry([TimingHandler(db)], load_plugins=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_team_routes(router, db)
    register_fpga_lab_routes(router, db, registry)
    register_experiment_routes(router, db, registry)
    app = FastAPI(); app.include_router(router)
    with TestClient(app, headers={'Authorization':'Bearer test'}) as client:
        yield db, client, registry

def save(client, body=None, key='save'):
    response = client.post(BASE+'/designs', json=body or dict(name='Tiny camera', config=small()), headers={'Idempotency-Key':key})
    assert response.status_code == 201, response.text
    return response.json()

def execute(env, saved, key='run', complete=True):
    db, client, registry = env
    preview = client.post(BASE+'/designs/'+saved['id']+'/preview', json={k:saved[k] for k in ('project_id','revision','sha256')})
    assert preview.status_code == 200, preview.text
    preview = preview.json()
    created = client.post('/api/v1/experiments', json=preview['experiment'], headers={'Idempotency-Key':key})
    assert created.status_code == 201, created.text
    path = '/api/v1/experiments/'+created.json()['id']+'/executions'
    submitted = client.post(path,json={'preview_hash':preview['preview_hash']},headers={'Idempotency-Key':key})
    assert submitted.status_code == 202, submitted.text
    assert client.post(path,json={'preview_hash':preview['preview_hash']},headers={'Idempotency-Key':key}).json()['id'] == submitted.json()['id']
    identity = submitted.json()['id']
    if complete:
        run_once(db, Registry(), 'actual-simpy-test', job_id=identity, experiment_registry=registry)
    return identity

def test_independent_payload_and_timing_oracle():
    c = TimingConfig()
    assert c.frame_bytes == 3110400
    assert c.frame_bytes*c.fps == 93312000
    c = TimingConfig(width=8, height=1, frames=1, preload_frames=1, start_delay_us=10, propagation_us=5)
    result = simulate(c.model_dump())
    frame = result['frames'][0]
    # 12 bytes / 800 MBps = 15ns; (12+6) bytes / 150 MBps = 120ns.
    assert frame['tx_start_ns'] == 10015
    assert frame['tx_end_ns'] == 10135
    assert frame['arrival_ns'] == 15135
    assert all(0 <= e['ddr_bytes'] <= c.ddr_bytes and 0 <= e['fifo_bytes'] <= c.fifo_bytes for e in result['events'])

def test_seed_reproducibility_and_faults():
    config = small(jitter_us=20000, drop_every=2)
    a = simulate(config, 42)
    assert a == simulate(config, 42)
    assert a != simulate(config, 43)
    assert a['frames'][1]['loss_reason'] == 'injected_drop'
    assert a['frames'][1]['status'] == 'missing_timeout'
    assert all(e['id'] == i for i, e in enumerate(a['events']))
    assert [e['time_ns'] for e in a['events']] == sorted(e['time_ns'] for e in a['events'])

def test_supply_fifo_overflow_and_timeout():
    c = small(supply_delay_us=110000, read_MBps=1, link_Mbps=100000, timeout_us=100000)
    r = simulate(c)
    assert r['summary']['underflow_frames'] > 0
    assert r['frames'][1]['status'] == 'late_timeout'
    c = TimingConfig(width=1920, height=1080, fps=60, expected_fps=60, frames=12, ddr_bytes=3110400, link_Mbps=100).model_dump()
    r = simulate(c)
    assert r['summary']['overflow_frames'] > 0
    assert any(f['status'] == 'missing_timeout' for f in r['frames'])
    assert any(e['kind'] == 'ecu_timeout' for e in r['events'])

@pytest.mark.parametrize('changes', [dict(width=7,bits=10),dict(fifo_bytes=1),dict(frames=120,fps=1),dict(preload_frames=121),dict(deadline_us=200000,timeout_us=100000),dict(height=2160,frames=120),dict(bits=14),dict(fps=True)])
def test_reject_unsupported(changes):
    with pytest.raises(ValueError): TimingConfig(**changes)

def test_cancellation():
    with pytest.raises(AdapterCancelledBeforeDispatch): simulate(small(), should_cancel=lambda:True)

def test_revision_queue_artifacts_and_missing_adapter(env):
    db, client, registry = env
    first = save(client)
    assert save(client) == first
    original = copy.deepcopy(first)
    second = save(client,dict(name='Changed',config=small(link_Mbps=100),id=first['id'],expected_revision=1),key='second')
    assert second['revision'] == 2
    old = client.get(BASE+'/designs/'+first['id'],params={'revision':1}).json()
    assert old == original
    conflict = client.post(BASE+'/designs',json=dict(name='Conflict',config=small(),id=first['id'],expected_revision=1),headers={'Idempotency-Key':'conflict'})
    assert conflict.status_code == 409
    identity = execute(env,first)
    run = client.get('/api/v1/experiments/executions/'+identity).json()
    assert run['status'] == 'completed', run
    assert run['fidelity'] == 'behavioral_timing'
    assert run['summary']['received'] == 3
    trace_artifact = next(a for a in run['artifacts'] if a['name']=='timing-trace.json')
    trace = client.get(trace_artifact['download_url']).json()
    assert trace['design_ref']['sha256'] == first['sha256']
    assert trace['source']['sha256'] == first['source']['sha256']
    assert len(client.get(BASE+'/executions').json()['items']) == 1
    assert client.get('/api/v1/experiments/executions/'+identity+'/export').status_code == 200
    registry._handlers.clear()
    assert client.get(BASE+'/templates').json()['adapter_installed'] is False
    assert client.post(BASE+'/designs/'+first['id']+'/preview',json={k:first[k] for k in ('project_id','revision','sha256')}).status_code == 422
    assert client.get(BASE+'/designs/'+first['id'],params={'revision':1}).status_code == 200

def test_tampered_ref_and_cross_project(env):
    _, client, _ = env
    first = save(client)
    action = {k:first[k] for k in ('project_id','revision','sha256')}
    action['sha256'] = '0'*64
    assert client.post(BASE+'/designs/'+first['id']+'/preview',json=action).status_code == 409
    # A nonexistent project must not fall back to the default project.
    assert client.get(BASE+'/designs/'+first['id'],params={'revision':1,'project_id':'other'}).status_code in (403,404)

def test_queued_cancel(env):
    _, client, _ = env
    identity = execute(env,save(client),complete=False)
    assert client.post('/api/v1/experiments/executions/'+identity+'/cancel').status_code == 200
    assert client.get('/api/v1/experiments/executions/'+identity).json()['status'] == 'cancelled'

def test_project_roles_and_sdk(env):
    from axiom_sdk import AxiomClient
    _, client, _ = env
    design = save(client)
    other = client.post('/api/v1/projects',json={'name':'Another lab'}).json()['id']
    token = client.post('/api/v1/projects/default/tokens',json={'user_label':'read-only','role':'Viewer'}).json()['token']
    headers = {'Authorization':'Bearer '+token}
    assert client.get(BASE+'/designs',headers=headers).status_code == 200
    assert client.get(BASE+'/designs',params={'project_id':other},headers=headers).status_code == 404
    assert client.post(BASE+'/designs',json={'name':'No write'},headers={**headers,'Idempotency-Key':'no-write'}).status_code == 403
    assert client.post(BASE+'/designs/'+design['id']+'/preview',json={k:design[k] for k in ('project_id','revision','sha256')},headers=headers).status_code == 403
    sdk = AxiomClient('http://testserver',token='test',transport=client._transport)
    assert sdk.fpga_lab_design(design['id'],revision=1) == design
    assert len(sdk.fpga_lab_templates()['items']) == 4
    submitted = sdk.execute_fpga_lab_design(design,idempotency_key='sdk')
    run_once(env[0],Registry(),'sdk-simpy',job_id=submitted['id'],experiment_registry=env[2])
    exported = sdk.export_fpga_lab_execution(submitted['id'])
    assert exported == client.get('/api/v1/experiments/executions/'+submitted['id']+'/export').json()
    assert sdk.fpga_lab_executions()['items'][0]['id'] == submitted['id']

@pytest.mark.parametrize('preset', PRESETS)
def test_actual_full_resolution_presets(preset):
    key, _, changes = preset
    result = simulate(TimingConfig(**changes).model_dump())
    summary = result['summary']
    if key == 'normal':
        assert summary['received'] == 60 and summary['deadline_exceeded'] == summary['underflow_frames'] == summary['missing'] == 0
    elif key == 'bandwidth':
        assert summary['overflow_frames'] > 0 and summary['deadline_exceeded'] > 0
    elif key == 'supply':
        assert summary['underflow_frames'] == summary['deadline_exceeded'] == 60
    else:
        assert summary['missing'] == 6 and summary['deadline_exceeded'] > 0

def test_exact_buffer_admission_and_below_capacity():
    c = TimingConfig(width=8,height=1,frames=1,preload_frames=0,ddr_bytes=12,fifo_bytes=12)
    r = simulate(c.model_dump())
    assert r['summary']['received'] == 1
    assert r['summary']['peak_ddr_bytes'] == r['summary']['peak_fifo_bytes'] == 12
    r = simulate(c.model_copy(update={'ddr_bytes':11}).model_dump())
    assert r['summary']['overflow_frames'] == r['summary']['missing'] == 1
    assert r['frames'][0]['arrival_ns'] is None

def test_adapter_failure_is_not_a_successful_simulation(env, monkeypatch):
    def fail(*args): raise RuntimeError('Intentional calculation failure for queue contract test')
    monkeypatch.setattr(env[2].get('simpy-camera-timing'), 'execute', fail)
    job = execute(env,save(env[1]))
    result = env[1].get('/api/v1/experiments/executions/'+job).json()
    assert result['status'] == 'failed'
    assert not result['summary']
