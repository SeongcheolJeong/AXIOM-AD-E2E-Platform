"""Engineering Lab source boundaries, immutable API and actual native worker contracts."""
import copy
import hashlib
import json
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from ade2e.adapters import Registry
from ade2e.database import Database, Settings
from ade2e.engineering_lab import register_engineering_lab_routes, templates_payload
from ade2e.engineering_lab_schemas import EngineeringTestSave
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow as Row
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once
from axiom_sdk import AxiomClient

BASE = '/api/v1/hil-sim/engineering-lab'
def definition(identity='passthrough'):
    p = copy.deepcopy(next(t for t in templates_payload()['items'] if t['id'] == identity)); p.pop('id'); return p

def action(t): return {k:t[k] for k in ['project_id', 'revision', 'sha256']}

@pytest.fixture
def env(tmp_path):
    settings=Settings(data_dir=tmp_path,api_token='test',team_mode=False,seed_examples=False)
    db=Database(settings);db.initialize();registry=ExperimentRegistry([],load_plugins=False)
    def auth(request:Request):
        header=request.headers.get('authorization','');authenticate_request(request,db,settings,header[7:] if header.startswith('Bearer ') else None)
    router=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
    register_team_routes(router,db);register_engineering_lab_routes(router,db,registry);register_experiment_routes(router,db,registry)
    app=FastAPI();app.include_router(router)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        yield db,client,registry,AxiomClient('http://testserver',token='test',transport=client._transport)

@pytest.mark.parametrize('identity', [p['id'] for p in templates_payload()['items']])
def test_templates_are_structurally_valid(identity):
    assert EngineeringTestSave.model_validate(definition(identity)).config.engine in ['rtl','spice','qualcomm','isp_rtl','adas_module_rtl','adas_validation_campaign','adas_preboard_checks']

@pytest.mark.parametrize('text', [
    '`include "other.sv"', '$system("touch sentinel");', '$readmemh("file",data);',
    '$display("x");', '$fopen("x");', '$c("host code");',
    'import "DPI-C" function void exec();', '/* verilator public */', '#include <stdio.h>',
    '`define A $sys\n`A``tem("x")', 'module 中文; endmodule', 'bad\x00text',
    '`timescale 1ns/1ps\nmodule user_top; endmodule', '`timescale 1ps/1ps\nmodule user_top; endmodule',
    'module user_top; timeunit 1ps; endmodule', 'module user_top; timeprecision 1ns; endmodule',
])
def test_unsafe_or_unsupported_rtl_sources_fail_closed(text):
    d=definition();d['config']['sources'][0]['content']=text
    with pytest.raises(ValueError):EngineeringTestSave.model_validate(d)

@pytest.mark.parametrize('name', ['../user_top.sv','/tmp/a.sv','user_top.cpp','a/b.v','a.sv;touch'])
def test_source_basenames_are_bounded(name):
    d=definition();d['config']['sources'][0]['name']=name
    with pytest.raises(ValueError):EngineeringTestSave.model_validate(d)


def test_multifile_sizes_uniqueness_and_allowed_functions():
    d=definition();source=d['config']['sources'][0]
    source['content']='`timescale 1ns/1ns\n`default_nettype none\nmodule user_top; localparam X=$clog2(64); endmodule\n'
    assert EngineeringTestSave.model_validate(d).config.sources
    d['config']['sources'].append(dict(name='USER_TOP.sv',content='module x;endmodule'))
    with pytest.raises(ValueError,match='unique'):EngineeringTestSave.model_validate(d)
    d=definition();d['config']['sources']=[dict(name='a.sv',content=' '*140000),dict(name='b.sv',content=' '*140000)]
    with pytest.raises(ValueError,match='256 KiB'):EngineeringTestSave.model_validate(d)

@pytest.mark.parametrize('line', [
    '.include model.lib','.lib x','.control','.tran 1u 1m','.save v(out)', '.param tau=1',
    '.model m NMOS(VTO=1)', 'B1 out 0 V=1', 'X1 in out hidden', 'V1 in 0 PWL(file=data.txt)',
    'R1 in out /tmp/model', 'R1 in out 1k; shell touch x', '+ continuation',
])
def test_spice_external_or_unsupported_statements_rejected(line):
    d=definition('rc-step');d['config']['netlist']='* explicit title\n'+line+'\n.end\n'
    with pytest.raises(ValueError):EngineeringTestSave.model_validate(d)


def test_spice_probe_schedule_and_inline_diode():
    d=definition('rc-step');d['config']['netlist']='* diode test\nV1 in 0 DC 1\nR1 in out 1k\nD1 out 0 simple\n.model simple D(Is=1e-14 N=1)\n.end\n';d['config']['probes']=['v(out)','i(V1)']
    assert EngineeringTestSave.model_validate(d).config.probes==['v(out)','i(V1)']
    for changes in [dict(step_s=1e-12),dict(stop_s=1e-9),dict(probes=['v(out);shell']),dict(probes=['v(out)','v(out)']),dict(netlist='.control\nshell touch x\n')]:
        bad=copy.deepcopy(d);bad['config'].update(changes)
        with pytest.raises(ValueError):EngineeringTestSave.model_validate(bad)


def test_immutable_source_hashes_sdk_and_unavailable_runtime(env):
    db,c,registry,sdk=env;d=definition()
    t=sdk.save_engineering_lab_test(**d,idempotency_key='source')
    assert sdk.save_engineering_lab_test(**d,idempotency_key='source')==t
    assert t['source_hashes']['user_top.sv']==hashlib.sha256(d['config']['sources'][0]['content'].encode()).hexdigest()
    assert sdk.engineering_lab_test(t['id'],revision=1)==t
    assert sdk.engineering_lab_tests()['items']==[t]
    assert sdk.engineering_lab_readiness()['available'] is False
    assert c.post(BASE+'/tests/'+t['id']+'/preview',json=action(t)).status_code==422
    q=sdk.save_engineering_lab_test(**definition('qualcomm-sa8650'),idempotency_key='qualcomm')
    assert c.post(BASE+'/tests/'+q['id']+'/preview',json=action(q)).status_code==422
    altered=copy.deepcopy(d);altered['config']['sources'][0]['content']+='\n// changed source\n'
    second=sdk.save_engineering_lab_test(**altered,test_id=t['id'],expected_revision=1,idempotency_key='source-edit')
    assert second['config_hash']!=t['config_hash'] and second['source_hashes']!=t['source_hashes']
    r=c.post(BASE+'/tests',json=altered|dict(id=t['id'],expected_revision=1),headers={'Idempotency-Key':'stale'})
    assert r.status_code==409
    assert c.get(BASE+'/tests/'+t['id'],params={'revision':1,'project_id':'other'}).status_code in [403,404]
    assert c.post(BASE+'/tests/'+t['id']+'/preview',json=action(t)|{'sha256':'0'*64}).status_code==409
    with db.session() as session:
        row=session.get(Row,('default','engineering-test',t['id'],1));doc=json.loads(row.manifest);doc['config']['sources'][0]['content']='tampered';row.manifest=json.dumps(doc).encode();session.commit()
    assert c.get(BASE+'/tests/'+t['id'],params={'revision':1}).status_code==409


def test_native_rtl_worker_sdk_and_qualcomm_fail_closed(env,monkeypatch,tmp_path):
    from axiom_engineering import EngineeringHandler
    monkeypatch.setenv('ADE2E_ENABLE_LOCAL_RTL','1');monkeypatch.setenv('ADE2E_TEAM_MODE','0')
    db,c,registry,sdk=env;handler=EngineeringHandler(db);registry.register(handler)
    d=definition();d['config']['words']=32
    t=sdk.save_engineering_lab_test(**d,idempotency_key='native')
    preview=sdk.preview_engineering_lab_test(t)
    job=sdk.execute_engineering_lab_test(t,idempotency_key='native-run')
    assert sdk.execute_engineering_lab_test(t,idempotency_key='native-run')['id']==job['id']
    run_once(db,Registry(load_plugins=False),'engineering-native-test',job_id=job['id'],experiment_registry=registry)
    run=sdk.experiment_execution(job['id']);assert run['status']=='completed',run
    assert run['summary']['verdict']=='passed'
    for a in run['artifacts']:
        path=sdk.download_experiment_artifact(job['id'],a['id'],tmp_path/a['name'])
        assert hashlib.sha256(path.read_bytes()).hexdigest()==a['sha256']
    assert sdk.export_engineering_lab_execution(job['id'])['execution']['id']==job['id']
    assert sdk.engineering_lab_executions()['items'][0]['test_ref']['sha256']==t['sha256']
    q=sdk.save_engineering_lab_test(**definition('qualcomm-sa8650'),idempotency_key='qualcomm')
    r=c.post(BASE+'/tests/'+q['id']+'/preview',json=action(q));assert r.status_code==422,r.text
    monkeypatch.setenv('ADE2E_TEAM_MODE','1')
    assert c.post(BASE+'/tests/'+t['id']+'/preview',json=action(t)).status_code==422
    monkeypatch.setenv('ADE2E_TEAM_MODE','0')
    db.team_mode=True  # Explicit Settings(team_mode=True) must also block the generic API.
    assert sdk.engineering_lab_readiness()['engines']['rtl']['available'] is False
    assert c.post(BASE+'/tests/'+t['id']+'/preview',json=action(t)).status_code==422
    assert c.post('/api/v1/experiments',json=preview['experiment'],headers={'Idempotency-Key':'shared-generic'}).status_code==422


def test_native_spice_worker_and_source_bound_export(env,tmp_path):
    from axiom_engineering import EngineeringHandler
    db,c,registry,sdk=env;registry.register(EngineeringHandler(db))
    t=sdk.save_engineering_lab_test(**definition('rc-step'),idempotency_key='spice')
    job=sdk.execute_engineering_lab_test(t,idempotency_key='spice-run')
    run_once(db,Registry(load_plugins=False),'engineering-spice-test',job_id=job['id'],experiment_registry=registry)
    run=sdk.experiment_execution(job['id']);assert run['status']=='completed',run
    assert run['summary']['verdict']=='observed'
    trace_artifact=next(a for a in run['artifacts'] if a['name']=='trace.json')
    path=sdk.download_experiment_artifact(job['id'],trace_artifact['id'],tmp_path/'trace.json')
    trace=json.loads(path.read_bytes())
    assert trace['engine']=='ngspice' and trace['axis']['unit']=='s'
    assert trace['provenance']['netlist_sha256']==t['netlist_sha256']
    sample=min(trace['samples'],key=lambda s:abs(s['time']-.001))
    assert .62 < sample['v(out)'] < .65
    assert sdk.export_engineering_lab_execution(job['id'])['execution']['id']==job['id']


def test_queued_invalid_rtl_exposes_bounded_native_syntax_diagnostic(env,monkeypatch):
    from axiom_engineering import EngineeringHandler
    monkeypatch.setenv('ADE2E_ENABLE_LOCAL_RTL','1');monkeypatch.setenv('ADE2E_TEAM_MODE','0')
    db,c,registry,sdk=env;registry.register(EngineeringHandler(db))
    d=definition()
    source=d['config']['sources'][0]
    source['content']=source['content'].replace('assign out_data = in_data;', 'assign out_data = ;')
    t=sdk.save_engineering_lab_test(**d,idempotency_key='bad-syntax')
    job=sdk.execute_engineering_lab_test(t,idempotency_key='bad-syntax-run')
    run_once(db,Registry(load_plugins=False),'engineering-syntax-test',job_id=job['id'],experiment_registry=registry)
    run=sdk.experiment_execution(job['id'])
    assert run['status']=='failed',run
    assert run['failure_http_status']==422
    diagnostic=str(run['error'])
    assert 'syntax error' in diagnostic.lower()
    assert 'user_top.sv' in diagnostic
    assert 'compile_failed' in diagnostic
    assert len(diagnostic)<6500
    assert run['summary'] is None


def test_handler_dispatched_cancellation_passes_through_native_diagnostic_boundary(env,monkeypatch,tmp_path):
    from ade2e.adapters import AdapterCancelled
    from ade2e.experiment_schemas import ExperimentCreate
    from axiom_engineering import EngineeringHandler,rtl
    monkeypatch.setenv('ADE2E_ENABLE_LOCAL_RTL','1');monkeypatch.setenv('ADE2E_TEAM_MODE','0')
    db,c,registry,sdk=env;handler=EngineeringHandler(db);registry.register(handler)
    t=sdk.save_engineering_lab_test(**definition(),idempotency_key='cancel-callback')
    preview=sdk.preview_engineering_lab_test(t)
    snapshot=handler.prepare(ExperimentCreate.model_validate(preview['experiment']))
    checks=0;reached_native=False
    def should_cancel():
        nonlocal checks
        checks+=1
        return checks>1
    def cancelled_native(config,work_dir,check,tool_identity,seed):
        nonlocal reached_native
        reached_native=True
        check()
        pytest.fail('Cancellation callback did not abort native dispatch')
    monkeypatch.setattr(rtl,'run',cancelled_native)
    with pytest.raises(AdapterCancelled) as error:
        handler.execute(snapshot,tmp_path/'cancelled-native',should_cancel)
    assert type(error.value) is AdapterCancelled  # Already dispatched, not BeforeDispatch.
    assert reached_native and checks==2
