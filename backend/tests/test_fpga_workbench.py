"""Graph authoring, compilation boundaries, immutable SDK and real worker contracts."""
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
from ade2e.fpga_workbench import register_fpga_workbench_routes, templates_payload, validate_bench, execution_hash
from ade2e.fpga_workbench_schemas import WorkbenchSave, ADAPTER
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow as Row
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once
from axiom_sdk import AxiomClient

BASE = '/api/v1/hil-sim/fpga-workbench'

def definition(index=1):
    p = copy.deepcopy(templates_payload()['items'][index])
    p.pop('id')
    return WorkbenchSave.model_validate(p).model_dump(exclude={'id', 'expected_revision'})

@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, api_token='test', team_mode=True, seed_examples=False)
    db = Database(settings); db.initialize()
    registry = ExperimentRegistry([], load_plugins=False)
    def auth(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    register_team_routes(router, db)
    register_fpga_workbench_routes(router, db, registry)
    register_experiment_routes(router, db, registry)
    app = FastAPI(); app.include_router(router)
    with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
        sdk = AxiomClient('http://testserver', token='test', transport=client._transport)
        yield db, client, registry, sdk

def save(env, d=None, key='save', **extra):
    d = d or definition()
    r = env[1].post(BASE+'/benches', json=d | extra, headers={'Idempotency-Key': key})
    assert r.status_code == 201, r.text
    return r.json()

def action(bench):
    return {k: bench[k] for k in ['project_id', 'revision', 'sha256']}

@pytest.mark.parametrize('index', range(4))
def test_templates_compile_to_exact_serial_order(index):
    d = definition(index); result = validate_bench(d)
    assert result['valid'], result
    c = result['compiled']
    assert c['fifo_order'] == (['fifo1'] if index == 0 else ['fifo1', 'fifo2'])
    assert c['depths'] == [64] * len(c['fifo_order'])
    assert c['stream_edges'] == ['stream'+str(i) for i in range(len(c['fifo_order'])+1)]
    assert c['source_id'] == 'source' and c['sink_id'] == 'sink' and c['ready_id'] == 'ready'
    assert c['ready_edge_id'] == 'ready_sink'
    assert c['execution_hash'] == execution_hash(d)


def test_execution_hash_ignores_presentation_but_preserves_logic():
    d = definition(); before = execution_hash(d); edited = copy.deepcopy(d)
    edited['name'] = 'Moved and renamed'
    for n in edited['graph']['nodes']:
        n['position']['x'] += 73.; n['label'] += ' renamed'
    edited['graph']['nodes'].reverse(); edited['graph']['edges'].reverse()
    edited['probes'].pop(); edited['panel_order'] = [p['id'] for p in reversed(edited['probes'])]
    assert execution_hash(edited) == before
    for change in ['depth', 'seed', 'resets', 'stalls', 'edge']:
        mutated = copy.deepcopy(d)
        if change == 'depth': mutated['graph']['nodes'][1]['params']['depth'] = 3
        elif change == 'seed': mutated['seed'] += 1
        elif change == 'resets': mutated['settings']['resets'].append(160)
        elif change == 'stalls': mutated['graph']['nodes'][-1]['params']['stalls'] = [dict(start_cycle=128, end_cycle=384)]
        else: mutated['graph']['edges'][0]['target'] = 'fifo2'
        assert execution_hash(mutated) != before


@pytest.mark.parametrize('case,expected', [
    ('missing_edge', 'serial_connection'), ('orphan_edge', 'missing_endpoint'),
    ('fanout', 'serial_connection'), ('fanin', 'serial_connection'),
    ('wrong_port', 'invalid_port'), ('missing_ready', 'ready_connection'),
    ('duplicate_source', 'node_count'), ('fifth_fifo', 'fifo_count'),
    ('orphan_probe', 'orphan_probe'), ('unsupported_signal', 'unsupported_signal'),
    ('orphan_panel', 'orphan_panel'), ('bad_schedule', 'invalid_schedule'),
])
def test_incomplete_or_unsupported_graphs_return_targeted_errors(case, expected):
    d = definition(); g = d['graph']
    if case == 'missing_edge': g['edges'].pop(0)
    elif case == 'orphan_edge': g['edges'][0]['target'] = 'missing'
    elif case == 'fanout': g['edges'].append(dict(g['edges'][0], id='fan', target='fifo2'))
    elif case == 'fanin': g['edges'].append(dict(g['edges'][1], id='fan', target='fifo1'))
    elif case == 'wrong_port': g['edges'][-1]['target'] = 'fifo1'
    elif case == 'missing_ready': g['edges'].pop()
    elif case == 'duplicate_source': g['nodes'].append(dict(g['nodes'][0], id='extra_source'))
    elif case == 'fifth_fifo':
        for i in range(3): g['nodes'].append(dict(g['nodes'][1], id='extra_fifo'+str(i)))
    elif case == 'orphan_probe': d['probes'][0]['target_id'] = 'missing'
    elif case == 'unsupported_signal': d['probes'][0]['signals'] = ['imaginary']
    elif case == 'orphan_panel': d['panel_order'].append('missing')
    elif case == 'bad_schedule': g['nodes'][-1]['params']['stalls'] = [dict(start_cycle=100, end_cycle=200), dict(start_cycle=150, end_cycle=300)]
    # These are saveable structural drafts; validation provides navigable problems.
    normalized = WorkbenchSave.model_validate(d).model_dump()
    result = validate_bench(normalized)
    assert not result['valid'] and result['compiled'] is None
    assert expected in {e['code'] for e in result['errors']}
    assert all(set(e) == {'code', 'message', 'node_ids', 'edge_ids', 'probe_ids'} for e in result['errors'])


def test_cycle_and_disconnected_fifo_rejected():
    d = definition()
    # A self-loop disconnected from the working source→fifo1→sink path.
    d['graph']['edges'][1]['target'] = 'sink'
    d['graph']['edges'][2].update(source='fifo2', target='fifo2')
    result = validate_bench(d)
    assert not result['valid']
    problem = next(e for e in result['errors'] if e['code'] == 'disconnected_path')
    assert 'fifo2' in problem['node_ids']


@pytest.mark.parametrize('instrument,target_type,target_id,signals,code', [
    ('buffer', 'node', 'source', ['out_valid'], 'invalid_buffer_probe'),
    ('buffer', 'edge', 'stream0', ['valid'], 'invalid_buffer_probe'),
    ('buffer', 'node', 'ready', ['value'], 'invalid_buffer_probe'),
    ('buffer', 'node', 'fifo1', ['full', 'empty'], 'invalid_buffer_probe'),
    ('buffer', 'node', 'fifo1', ['occupancy', 'in_data'], 'invalid_buffer_probe'),
    ('counter', 'node', 'source', ['out_data'], 'invalid_counter_probe'),
    ('counter', 'edge', 'stream0', ['data'], 'invalid_counter_probe'),
    ('counter', 'node', 'fifo1', ['occupancy_after'], 'invalid_counter_probe'),
])
def test_invalid_instrument_binding_saveable_but_execution_rejected(env, instrument, target_type, target_id, signals, code):
    d = definition()
    d['probes'][0].update(instrument=instrument, target_type=target_type, target_id=target_id, signals=signals)
    b = save(env, d)
    result = env[3].validate_fpga_workbench(**d)
    problem = next(e for e in result['errors'] if e['code'] == code)
    assert problem['probe_ids'] == ['fifo_buffer']
    assert not result['valid'] and result['compiled'] is None
    r = env[1].post(BASE+'/benches/'+b['id']+'/preview', json=action(b))
    assert r.status_code == 422 and r.json()['detail']['valid'] is False


def test_boolean_counters_and_explicit_occupancy_buffer_bindings():
    d = definition()
    d['probes'][0]['signals'] = ['full', 'occupancy_after', 'empty']
    d['probes'][1].update(instrument='counter', signals=['valid', 'ready', 'accepted'])
    assert validate_bench(d)['valid']
    d['probes'][1].update(target_type='node', target_id='ready', signals=['value'])
    assert validate_bench(d)['valid']


def test_save_incomplete_preflight_fails_and_sdk_revision_contract(env):
    _, client, _, sdk = env
    d = definition(); d['graph']['edges'].pop(0)
    b = sdk.save_fpga_workbench(**d, idempotency_key='draft')
    assert sdk.fpga_workbench(b['id'], revision=1) == b
    validation = sdk.validate_fpga_workbench(**d)
    assert not validation['valid']
    p = client.post(BASE+'/benches/'+b['id']+'/preview', json=action(b))
    assert p.status_code == 422 and p.json()['detail']['valid'] is False
    assert sdk.fpga_workbench_executions()['items'] == []
    assert sdk.fpga_workbench_readiness()['available'] is False
    assert save(env, d, key='draft') == b
    assert client.post(BASE+'/benches', json=d | {'name': 'changed'}, headers={'Idempotency-Key': 'draft'}).status_code == 409
    d = definition(); moved = copy.deepcopy(d); moved['graph']['nodes'][1]['position']['x'] += 20.
    good = save(env, d, key='good')
    second = save(env, moved, key='moved', id=good['id'], expected_revision=1)
    assert good['sha256'] != second['sha256'] and good['execution_hash'] == second['execution_hash']
    assert client.post(BASE+'/benches', json=d | {'id': good['id'], 'expected_revision': 1}, headers={'Idempotency-Key': 'stale'}).status_code == 409
    assert sdk.fpga_workbench(good['id'], revision=1) == good
    assert client.get(BASE+'/benches/'+b['id'], params={'project_id': 'other', 'revision': 1}).status_code in [403, 404]
    assert client.post(BASE+'/benches/'+good['id']+'/preview', json=action(good)).status_code == 422  # no fallback


def test_structural_invalid_ids_and_parameters_rejected(env):
    for variant in ['duplicate_node', 'duplicate_edge', 'depth', 'port', 'reset']:
        d = definition()
        if variant == 'duplicate_node': d['graph']['nodes'].append(d['graph']['nodes'][0])
        elif variant == 'duplicate_edge': d['graph']['edges'].append(d['graph']['edges'][0])
        elif variant == 'depth': d['graph']['nodes'][1]['params']['depth'] = 1
        elif variant == 'port': d['graph']['edges'][0]['source_port'] = 'write'
        else: d['settings']['resets'] = []
        assert env[1].post(BASE+'/benches', json=d, headers={'Idempotency-Key': variant}).status_code == 422


def test_saved_workbench_tamper_and_ref_hash_fail_closed(env):
    db, client, _, _ = env
    b = save(env)
    assert client.post(BASE+'/benches/'+b['id']+'/preview', json=action(b) | {'sha256': '0'*64}).status_code == 409
    with db.session() as session:
        row = session.get(Row, ('default', 'fpga-workbench', b['id'], 1)); doc = json.loads(row.manifest)
        doc['graph']['nodes'][1]['params']['depth'] = 5
        row.manifest = json.dumps(doc).encode(); session.commit()
    assert client.get(BASE+'/benches/'+b['id'], params={'revision': 1}).status_code == 409


def test_actual_workbench_worker_artifacts_sdk_and_cancel(env, tmp_path):
    from axiom_workbench import WorkbenchHandler
    db, client, registry, sdk = env
    handler = WorkbenchHandler(db)
    if not handler.readiness().get('available'): pytest.skip('Actual Verilator/compiler unavailable')
    registry.register(handler)
    d = definition(2); d['graph']['nodes'][0]['params']['words'] = 512
    b = save(env, d)
    preview = sdk.preview_fpga_workbench(b)
    assert preview['execution_hash'] == b['execution_hash']
    assert preview['experiment']['kind'] == 'fpga_workbench'
    job = sdk.execute_fpga_workbench(b, idempotency_key='native')
    assert sdk.execute_fpga_workbench(b, idempotency_key='native')['id'] == job['id']
    run_once(db, Registry(load_plugins=False), 'workbench-test', job_id=job['id'], experiment_registry=registry)
    result = sdk.experiment_execution(job['id'])
    assert result['status'] == 'completed', result
    assert result['summary']['verdict'] == 'passed'
    assert result['summary']['matched'] is True
    for artifact in result['artifacts']:
        path = sdk.download_experiment_artifact(job['id'], artifact['id'], tmp_path / artifact['name'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']
    assert sdk.fpga_workbench_executions()['items'][0]['bench_ref']['sha256'] == b['sha256']
    assert sdk.export_fpga_workbench_execution(job['id'])['execution']['id'] == job['id']
    bad = copy.deepcopy(preview['experiment']); bad['seed'] += 1
    assert client.post('/api/v1/experiments', json=bad, headers={'Idempotency-Key': 'wrong-seed'}).status_code == 422
    cancel = sdk.execute_fpga_workbench(b, idempotency_key='cancel')
    sdk.cancel_experiment(cancel['id'])
    assert sdk.experiment_execution(cancel['id'])['status'] == 'cancelled'
