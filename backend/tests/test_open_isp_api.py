"""Open ISP immutable input and separate pixel/build execution contracts."""
import copy
import hashlib
import json
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from ade2e.adapters import Registry
from ade2e.apollo_lab import register_raw_routes, asset_path
from ade2e.database import Database, Settings
from ade2e.engineering_lab import register_engineering_lab_routes
from ade2e.engineering_lab_schemas import IspRtlConfig, EngineeringTestSave
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.experiment_schemas import ExperimentCreate
from ade2e.team_access import authenticate_request, register_team_routes
from ade2e.worker import run_once
from axiom_sdk import AxiomClient

BASE='/api/v1/hil-sim/engineering-lab'


@pytest.fixture
def env(tmp_path):
    settings=Settings(data_dir=tmp_path,api_token='test',team_mode=False,seed_examples=False)
    db=Database(settings);db.initialize();registry=ExperimentRegistry([],load_plugins=False)
    def auth(request:Request):
        header=request.headers.get('authorization','')
        authenticate_request(request,db,settings,header[7:] if header.startswith('Bearer ') else None)
    router=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
    register_team_routes(router,db)
    register_raw_routes(router,db,'/hil-sim/apollo-lab')
    register_engineering_lab_routes(router,db,registry)
    register_experiment_routes(router,db,registry)
    app=FastAPI();app.include_router(router)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        yield db,client,registry,AxiomClient('http://testserver',token='test',transport=client._transport)


def config(**changes):
    return IspRtlConfig(engine='isp_rtl',width=16,height=16,max_cycles=5000,**changes).model_dump()


def save(sdk, definition=None, key='isp'):
    return sdk.save_engineering_lab_test(name='Open ISP contract',config=definition or config(),idempotency_key=key)


def ref(value):
    return {k:value[k] for k in ('id','revision','sha256')}


@pytest.mark.parametrize('change',[
    {'width':9}, {'height':241}, {'width':322}, {'frames':3}, {'black_level':4096},
    {'gains':[128,128,128]}, {'gains':[128,128,128,1.5]}, {'gains':[65536]*4},
    {'pipeline_id':'external'}, {'source_kind':'raw_asset'}, {'raw_frame_index':1},
    {'max_cycles':100,'reset_at_cycle':100}, {'stalls':[{'start_cycle':7,'end_cycle':7}]},
    {'stalls':[{'start_cycle':9,'end_cycle':15},{'start_cycle':8,'end_cycle':11}]},
    {'sources':[{'name':'arbitrary.sv','content':'module x;endmodule'}]},
])
def test_isp_config_rejects_unsupported_sources_and_schedules(change):
    value=config();value.update(change)
    with pytest.raises(ValueError):IspRtlConfig.model_validate(value)


def test_isp_raw_reference_shape_range_and_revision(env):
    db,client,registry,sdk=env
    raw=bytes([64,0])*16*16
    metadata=dict(width=16,height=16,bits=12,packing='uint16le',bayer='RGGB')
    asset=sdk.upload_apollo_lab_raw(raw,metadata=metadata)
    definition=config(source_kind='raw_asset',raw_asset_ref=ref(asset))
    test=save(sdk,definition)
    assert test['config']['raw_asset_ref']==ref(asset)
    assert sdk.engineering_lab_test(test['id'],revision=1)==test
    for changes, expected in [({'width':8},422),({'bayer_pattern':'BGGR'},422),({'frames':2},422),
                              ({'raw_asset_ref':ref(asset)|{'sha256':'0'*64}},409)]:
        response=client.post(BASE+'/tests',json=dict(name='bad RAW',config=definition|changes),
            headers={'Idempotency-Key':str(changes)})
        assert response.status_code==expected,response.text
    missing=definition|{'raw_asset_ref':ref(asset)|{'id':'raw_missing'}}
    response=client.post(BASE+'/tests',json=dict(name='missing RAW',config=missing),headers={'Idempotency-Key':'missing'})
    assert response.status_code==404


def test_actual_worker_pixel_run_raw_input_and_export(env,tmp_path):
    from axiom_engineering import EngineeringHandler
    db,client,registry,sdk=env;registry.register(EngineeringHandler(db))
    if not sdk.engineering_lab_readiness().get('engines',{}).get('isp_rtl',{}).get('available'):
        pytest.skip('Native Open ISP Verilator adapter unavailable')
    raw=bytes([64,0])*16*16
    asset=sdk.upload_apollo_lab_raw(raw,metadata=dict(width=16,height=16,bits=12,packing='uint16le',bayer='RGGB'))
    test=save(sdk,config(source_kind='raw_asset',raw_asset_ref=ref(asset)))
    job=sdk.execute_engineering_lab_test(test,idempotency_key='isp-native')
    run_once(db,Registry(load_plugins=False),'isp-api-native',job_id=job['id'],experiment_registry=registry)
    result=sdk.experiment_execution(job['id'])
    assert result['status']=='completed',result
    assert result['summary']['verdict']=='passed'
    assert result['summary']['operation']=='simulate'
    assert result['summary']['validation_domain']=='pixels'
    assert result['summary']['input_sha256']==hashlib.sha256(raw).hexdigest()
    assert result['fidelity']=='open_isp_rtl_pixel_reference'
    assert sdk.export_engineering_lab_execution(job['id'])['execution']['id']==job['id']
    artifact=next(a for a in result['artifacts'] if a['name']=='provenance.json')
    path=sdk.download_experiment_artifact(job['id'],artifact['id'],tmp_path/'provenance.json')
    provenance=json.loads(path.read_bytes())
    assert provenance['raw_asset']['content_sha256']==asset['content_sha256']
    assert provenance['test_ref']==ref(test)
    history=sdk.engineering_lab_executions()['items'][0]
    assert history['operation']=='simulate' and history['engine']=='isp_rtl'
    assert history['design_hash']==result['summary']['design_hash']
    assert history['input_hash']==hashlib.sha256(raw).hexdigest()
    asset_path(db,asset).write_bytes(bytes(len(raw)))
    response=client.post(BASE+'/tests/'+test['id']+'/preview',json={k:test[k] for k in ('project_id','revision','sha256')})
    assert response.status_code==422 and 'hash' in response.text


def test_build_is_separate_domain_and_unconfigured_profiles_create_no_job(env):
    from axiom_engineering import EngineeringHandler
    from axiom_isp_rtl.handler import BuildHandler
    db,client,registry,sdk=env
    registry.register(EngineeringHandler(db));registry.register(BuildHandler(db))
    test=save(sdk)
    profiles=sdk.engineering_lab_readiness()['build_profiles']
    assert {p['id'] for p in profiles}=={'ecp5-85k-cabga381-6-ooc/1','gw2a-gowin-template/1','cyclonev-quartus-template/1'}
    for profile in ['gw2a-gowin-template/1','cyclonev-quartus-template/1','unknown']:
        response=client.post(BASE+'/tests/'+test['id']+'/build-preview',json={k:test[k] for k in ('project_id','revision','sha256')}|{'build_profile_id':profile})
        assert response.status_code==422,response.text
    assert sdk.engineering_lab_executions()['items']==[]
    if not next(p for p in profiles if p['id']=='ecp5-85k-cabga381-6-ooc/1')['available']:
        return
    preview=sdk.preview_engineering_lab_build(test)
    assert preview['experiment']['kind']=='fpga_build'
    assert preview['experiment']['adapter_id']=='open-isp-ecp5-build'
    job=sdk.execute_engineering_lab_build(test,idempotency_key='separate-build')
    assert job['experiment_kind']=='fpga_build'
    history=sdk.engineering_lab_executions()['items'][0]
    assert history['operation']=='build' and history['engine']=='isp_rtl'
    assert history['build_profile_id']=='ecp5-85k-cabga381-6-ooc/1'
    assert job.get('summary') is None  # Queuing a build establishes no validation pass.
    run_once(db,Registry(load_plugins=False),'isp-api-build',job_id=job['id'],experiment_registry=registry)
    result=sdk.experiment_execution(job['id'])
    assert result['status']=='completed',result
    assert result['fidelity']=='fpga_ooc_implementation'
    assert result['summary']['operation']=='build'
    assert result['summary']['validation_domain']=='fpga_ooc'
    assert result['summary']['pixel_validation']=='not_evaluated'
    assert result['summary']['design_hash']==history['design_hash']
    assert sdk.export_engineering_lab_execution(job['id'])['execution']['id']==job['id']


@pytest.mark.parametrize('changed',['tool','prepared_design','returned_design'])
def test_dispatch_refuses_identity_drift_without_publishing_wrong_design(env,tmp_path,monkeypatch,changed):
    """Mocked guard checks only; actual pixel and FPGA execution are tested above."""
    from fastapi import HTTPException
    import axiom_isp_rtl
    from axiom_engineering import EngineeringHandler
    db,client,registry,sdk=env;handler=EngineeringHandler(db);registry.register(handler)
    test=save(sdk)
    preview=sdk.preview_engineering_lab_test(test)
    snapshot=handler.prepare(ExperimentCreate.model_validate(preview['experiment']))
    native_calls=[]
    def fake_native(*args):
        native_calls.append(True)
        return {'summary':{'design_hash':'0'*64}}
    monkeypatch.setattr(axiom_isp_rtl,'run',fake_native)
    if changed=='tool':
        original=axiom_isp_rtl.identity
        monkeypatch.setattr(axiom_isp_rtl,'identity',lambda:original()|{'changed':True})
    elif changed=='prepared_design':
        snapshot['design_hash']='0'*64
    with pytest.raises(HTTPException) as error:
        handler.execute(snapshot,tmp_path/'mismatched',lambda:False)
    assert error.value.status_code==422
    assert ('identity changed' in error.value.detail if changed!='returned_design' else 'design hash differs' in error.value.detail)
    assert len(native_calls)==(1 if changed=='returned_design' else 0)
    assert not (tmp_path/'mismatched'/'summary.json').exists()


def test_queued_raw_tamper_fails_before_native_execution(env):
    from axiom_engineering import EngineeringHandler
    db,client,registry,sdk=env;registry.register(EngineeringHandler(db))
    raw=bytes([64,0])*16*16
    asset=sdk.upload_apollo_lab_raw(raw,metadata=dict(width=16,height=16,bits=12,packing='uint16le',bayer='RGGB'))
    test=save(sdk,config(source_kind='raw_asset',raw_asset_ref=ref(asset)))
    job=sdk.execute_engineering_lab_test(test,idempotency_key='raw-before-tamper')
    asset_path(db,asset).write_bytes(bytes(len(raw)))
    run_once(db,Registry(load_plugins=False),'isp-api-tamper',job_id=job['id'],experiment_registry=registry)
    result=sdk.experiment_execution(job['id'])
    assert result['status']=='failed' and result['failure_http_status']==422
    assert 'RAW original hash changed' in result['error']
    assert result.get('summary') is None and result['artifacts']==[]


@pytest.mark.parametrize('outcome',['cancel','ownership'])
def test_dispatched_stop_callback_is_not_converted_or_repolled(env,tmp_path,monkeypatch,outcome):
    """Typed callback contract; a one-shot stop must survive the adapter boundary."""
    import axiom_isp_rtl
    from ade2e.adapters import AdapterCancelled, AdapterOwnershipLost
    from axiom_engineering import EngineeringHandler
    db,client,registry,sdk=env;handler=EngineeringHandler(db);registry.register(handler)
    test=save(sdk)
    preview=sdk.preview_engineering_lab_test(test)
    snapshot=handler.prepare(ExperimentCreate.model_validate(preview['experiment']))
    checks=0
    expected=AdapterCancelled if outcome=='cancel' else AdapterOwnershipLost
    def stop_once():
        nonlocal checks
        checks+=1
        if checks==2:
            if outcome=='ownership':raise AdapterOwnershipLost('One-shot lost ownership')
            return True
        return False
    def native(config,work_dir,check,identity,seed):
        check()
        pytest.fail('Stop callback did not abort dispatch')
    monkeypatch.setattr(axiom_isp_rtl,'run',native)
    with pytest.raises(expected) as error:
        handler.execute(snapshot,tmp_path/'stopped',stop_once)
    assert type(error.value) is expected
    assert checks==2
    assert not (tmp_path/'stopped'/'summary.json').exists()
