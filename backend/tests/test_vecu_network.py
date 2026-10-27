"""API/SDK queue-contract tests. Fixture adapter is not native engine evidence."""
import json
from pathlib import Path
import pytest
from fastapi import APIRouter, FastAPI, Depends, Request
from fastapi.testclient import TestClient
from ade2e.database import Database,Settings
from ade2e.team_access import authenticate_request,register_team_routes
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentRegistry,ExperimentHandlerMetadata,ExperimentExecution,ExperimentOutputFile
from ade2e.adapters import Registry
from ade2e.worker import run_once
from ade2e.vecu import register_vecu_routes,example
from ade2e.vecu_network import register_vecu_network_routes,resolve,ref
from ade2e.vecu_network_schemas import NetworkSave,NetworkTestSave,Fault
from ade2e.service import canonical_bytes,digest
from axiom_sdk import AxiomClient

class ContractHandler:
    def __init__(self,db):self.db=db;self.available=True;self.runtime_hash='a'*64
    def metadata(self):return ExperimentHandlerMetadata(id='vecu-network',version='fixture/1',kinds=['virtual_ecu'],status='available',fidelity='contract_fixture_only',cancellation_supported=True)
    def catalog(self):return {'profiles':[{'id':'actual_silkit_autoas_trampoline'}]}
    def readiness(self,profile_id):return {'available':self.available,'status':'available' if self.available else 'not_configured','profile_id':profile_id,'runtime':{'manifest_sha256':self.runtime_hash}}
    def prepare(self,spec):
        if not self.available:raise ValueError('Native engines are unavailable; no reference fallback')
        with self.db.read_session() as session:
            test=resolve(session,spec.project_id,'vecu-network-test',spec.input['test_ref'])
            network=resolve(session,spec.project_id,'vecu-network-config',test['network_ref'])
        return dict(schema_version='network-contract-fixture/1',test=test,network=network,runtime=self.readiness(network['profile_id'])['runtime'])
    def execute(self,snapshot,work_dir,should_cancel):
        summary={'schema_version':'network-contract-fixture/1','verdict':'fixture_only','physical_hardware_connected':False}
        path=Path(work_dir)/'network-contract.json';path.write_bytes(canonical_bytes(summary))
        artifacts=[ExperimentOutputFile(path,path.name,'application/json')]
        if getattr(self,'causality_fixture',None) is not None:
            trace=Path(work_dir)/'vecu-trace.json';trace.write_bytes(canonical_bytes(self.causality_fixture))
            artifacts.append(ExperimentOutputFile(trace,trace.name,'application/json'))
        return ExperimentExecution(summary=summary,artifacts=artifacts,fidelity='contract_fixture_only')

@pytest.fixture
def env(tmp_path):
    settings=Settings(data_dir=tmp_path,api_token='test',team_mode=False,seed_examples=False)
    db=Database(settings);db.initialize();handler=ContractHandler(db);registry=ExperimentRegistry([handler],load_plugins=False)
    def auth(request:Request):authenticate_request(request,db,settings,request.headers.get('authorization','')[7:])
    router=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
    register_team_routes(router,db);register_vecu_routes(router,db,registry);register_vecu_network_routes(router,db,registry);register_experiment_routes(router,db,registry)
    app=FastAPI();app.include_router(router)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        yield db,client,registry,AxiomClient('http://testserver',token='test',transport=client._transport)

def saved(env,kind='udp_delay'):
    sdk=env[-1]
    network=sdk.save_vecu_network_record('networks',dict(name='Four ECUs'),idempotency_key='network')
    source=sdk.save_vecu_record('inputs',example(),idempotency_key='source')
    test=sdk.save_vecu_network_record('tests',dict(name='Network A/B',network_ref=ref(network),input_ref=ref(source),faults=[dict(kind=kind)]),idempotency_key='test-'+kind)
    return network,source,test

@pytest.mark.parametrize('kind',['udp_delay','udp_drop','udp_repeat','can_command_drop','restbus_drop','ecu_reset','os_event_drop'])
def test_saved_faults_and_exact_refs(env,kind):
    network,source,test=saved(env,kind);sdk=env[-1]
    assert test['faults'][0]['start_ms']==3000 and test['faults'][0]['end_ms']==7000
    assert sdk.vecu_network_record('tests',test['id'],revision=1)==test
    preview=sdk.preview_vecu_network_test(test)
    assert preview['prepared']['input']['network']==network
    assert preview['prepared']['input']['runtime']['manifest_sha256']=='a'*64
    assert preview['experiment']['adapter_id']=='vecu-network'
    assert preview['experiment']['input']['test_ref']==ref(test)


def test_native_missing_preserves_authoring_but_rejects_execution(env):
    handler=env[2].get('vecu-network');handler.available=False;sdk=env[-1]
    _,_,test=saved(env)
    assert sdk.vecu_network_readiness()['available'] is False
    with pytest.raises(Exception,match='Native engines are unavailable'):sdk.execute_vecu_network_test(test,idempotency_key='missing')
    assert sdk.vecu_network_executions()['items']==[]


@pytest.mark.parametrize('collection',['networks','tests'])
def test_blank_name_is_rejected_before_persistence_and_trimmed_name_roundtrips(env,collection):
    _,client,_,sdk=env;network,source,_=saved(env)
    body={} if collection=='networks' else dict(network_ref=ref(network),input_ref=ref(source))
    before=sdk.vecu_network_records(collection)
    for index,name in enumerate(['   ','\t\n','\u3000']):
        response=client.post('/api/v1/virtual-ecu-network/'+collection,json=body|{'name':name},headers={'Idempotency-Key':f'blank-{collection}-{index}'})
        assert response.status_code==422 and 'name is required' in response.text
    assert sdk.vecu_network_records(collection)==before
    clean=sdk.save_vecu_network_record(collection,body|{'name':'  Trimmed name  '},idempotency_key='trimmed-'+collection)
    assert clean['name']=='Trimmed name'
    assert sdk.vecu_network_record(collection,clean['id'],revision=1)==clean


def test_adapter_absent_catalog_and_readiness_never_claim_native_support(env):
    env[2]._handlers.clear();sdk=env[-1]
    assert sdk.vecu_network_catalog()['authoring']=='available'
    readiness=sdk.vecu_network_readiness();assert not readiness['available'] and readiness['status']=='not_configured'
    assert 'not installed' in readiness['reason']
    _,_,test=saved(env)
    with pytest.raises(Exception):sdk.preview_vecu_network_test(test)


def test_revision_idempotency_hash_and_cross_project(env):
    network,source,test=saved(env);_,client,_,sdk=env
    assert sdk.save_vecu_network_record('networks',dict(name='Four ECUs'),idempotency_key='network')==network
    with pytest.raises(Exception,match='another request'):sdk.save_vecu_network_record('networks',dict(name='different'),idempotency_key='network')
    edited=sdk.save_vecu_network_record('networks',dict(name='revised',id=network['id'],expected_revision=1),idempotency_key='rev2')
    assert edited['revision']==2 and sdk.vecu_network_record('networks',network['id'],revision=1)==network
    assert sdk.preview_vecu_network_test(test)['prepared']['input']['network']==network
    with pytest.raises(Exception,match='Revision changed'):sdk.save_vecu_network_record('networks',dict(name='stale',id=network['id'],expected_revision=1),idempotency_key='stale')
    bad=dict(name='bad',network_ref=ref(network)|{'sha256':'b'*64},input_ref=ref(source))
    assert client.post('/api/v1/virtual-ecu-network/tests',json=bad,headers={'Idempotency-Key':'bad'}).status_code==409
    other=client.post('/api/v1/projects',json={'name':'Other'}).json()['id']
    assert client.get('/api/v1/virtual-ecu-network/records/networks/'+network['id'],params={'project_id':other,'revision':1}).status_code==404
    assert client.post('/api/v1/virtual-ecu-network/tests',json=bad|{'project_id':other},headers={'Idempotency-Key':'other'}).status_code==404


def test_normal_queue_cancel_export_and_contract_execution(env):
    db,client,registry,sdk=env;_,_,test=saved(env)
    job=sdk.execute_vecu_network_test(test,idempotency_key='queue')
    assert sdk.execute_vecu_network_test(test,idempotency_key='queue')['id']==job['id']
    assert sdk.vecu_network_executions()['items'][0]['test_ref']==ref(test)
    sdk.cancel_vecu_network_execution(job['id'])
    assert sdk.vecu_network_execution(job['id'])['status']=='cancelled'
    receipt=sdk.export_vecu_network_execution(job['id']);assert json.loads(receipt)['execution']['id']==job['id']
    job=sdk.execute_vecu_network_test(test,idempotency_key='fixture-run')
    run_once(db,Registry(load_plugins=False),'contract-worker',job_id=job['id'],experiment_registry=registry)
    result=sdk.vecu_network_execution(job['id']);assert result['status']=='completed' and result['summary']['verdict']=='fixture_only'
    exported=sdk.export_vecu_network_execution(job['id']);assert digest(json.loads(exported))==result['manifest_hash']
    other=client.post('/api/v1/projects',json={'name':'Other execution'}).json()['id']
    for suffix in ['', '/export']:
        assert client.get('/api/v1/virtual-ecu-network/executions/'+job['id']+suffix,params={'project_id':other}).status_code==404


def test_readonly_causality_is_project_scoped_hash_verified_and_shared_with_sdk(env):
    from test_vecu_network_causality import authored_case
    from ade2e.experiment_models import ExperimentArtifactRow
    from sqlalchemy import select
    db,client,registry,sdk=env;_,_,test=saved(env)
    trace,_=authored_case();trace['sides']['faulted'][60]['reply']['network']['events'][0]['fault']=test['faults'][0]
    registry.get('vecu-network').causality_fixture=trace
    job=sdk.execute_vecu_network_test(test,idempotency_key='causality-fixture')
    route='/api/v1/virtual-ecu-network/executions/'+job['id']+'/causality'
    assert client.get(route).status_code==409
    run_once(db,Registry(load_plugins=False),'contract-worker',job_id=job['id'],experiment_registry=registry)
    report=sdk.vecu_network_causality(job['id'])
    assert report['cases'][0]['stages']['detection']['time_ms']==3250
    assert report['fidelity']=='contract_fixture_only' and report['physical_validation'] is False
    assert report['sha256']==digest({k:v for k,v in report.items() if k!='sha256'})
    assert sdk.vecu_network_execution(job['id'])['manifest_hash']==report['manifest_sha256']
    other=client.post('/api/v1/projects',json={'name':'Other causality'}).json()['id']
    assert client.get(route,params={'project_id':other}).status_code==404
    with db.read_session() as session:
        artifact=session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==job['id'],ExperimentArtifactRow.name=='vecu-trace.json'))
        path=db.experiment_dir/'artifacts'/artifact.storage_key
    path.write_text('{}')
    assert client.get(route).status_code==409
    assert client.post('/api/v1/virtual-ecu-network/executions/'+job['id']+'/cancel',params={'project_id':other}).status_code==404


def test_project_roles_are_enforced(env):
    _,client,_,sdk=env;network,source,test=saved(env)
    def headers(role):
        token=client.post('/api/v1/projects/default/tokens',json={'user_label':role,'role':role}).json()['token']
        return {'Authorization':'Bearer '+token,'Idempotency-Key':'role-'+role}
    viewer=headers('Viewer');engineer=headers('Engineer')
    assert client.get('/api/v1/virtual-ecu-network/catalog',headers=viewer).status_code==200
    assert client.get('/api/v1/virtual-ecu-network/readiness',headers=viewer).status_code==200
    assert client.post('/api/v1/virtual-ecu-network/networks',json={'name':'forbidden'},headers=viewer).status_code==403
    action={key:test[key] for key in ['project_id','revision','sha256']}
    assert client.post('/api/v1/virtual-ecu-network/tests/'+test['id']+'/executions',json=action,headers=viewer).status_code==403
    response=client.post('/api/v1/virtual-ecu-network/tests/'+test['id']+'/executions',json=action,headers=engineer);assert response.status_code==202
    assert client.post('/api/v1/virtual-ecu-network/executions/'+response.json()['id']+'/cancel',headers=viewer).status_code==403


def test_carla_reference_seed_and_fault_window(env,monkeypatch):
    from ade2e import hil_beta
    _,client,_,sdk=env;network,source,_=saved(env)
    reference={'id':'hil_fixture','revision':1,'sha256':'c'*64}
    experiment={'seed':42,'kind':'hil_demo','adapter_id':'carla-software-dut','input':{'fixture':True}}
    monkeypatch.setattr(hil_beta,'_test',lambda *args: {'experiment':experiment})
    body=dict(name='CARLA network',network_ref=ref(network),mode='carla_closed_loop',hil_test_ref=reference,faults=[dict(kind='udp_drop')])
    test=sdk.save_vecu_network_record('tests',body,idempotency_key='carla')
    assert test['hil_experiment']==experiment
    assert client.post('/api/v1/virtual-ecu-network/tests',json=body|{'seed':1},headers={'Idempotency-Key':'seed'}).status_code==422
    assert client.post('/api/v1/virtual-ecu-network/tests',json=body|{'faults':[dict(kind='udp_drop',end_ms=18000)]},headers={'Idempotency-Key':'duration'}).status_code==422


@pytest.mark.parametrize('change',[{'roles':['adas']},{'can':{'command_id':2048}},{'can':{'status_id':256}},{'can':{'format':'can_fd'}},{'ethernet':{'source_ip':'::1'}},{'ethernet':{'destination_ip':'192.0.2.10'}},{'command':'arbitrary'},{'profile_id':'unknown'},{'tick_ms':2}])
def test_unsupported_network_fields_rejected(change):
    with pytest.raises(ValueError):NetworkSave(name='bad',**change)


def test_invalid_faults_and_mode_contract():
    reference={'id':'fixture','revision':1,'sha256':'a'*64}
    with pytest.raises(ValueError):Fault(kind='udp_delay',delay_ms=200)
    with pytest.raises(ValueError):Fault(kind='ecu_reset',start_ms=3000,end_ms=3100)
    with pytest.raises(ValueError):Fault(kind='udp_drop',target_ecu='brake')
    with pytest.raises(ValueError):NetworkTestSave(name='bad',network_ref=reference)
    with pytest.raises(ValueError):NetworkTestSave(name='bad',network_ref=reference,input_ref=reference,hil_test_ref=reference)
    with pytest.raises(ValueError):NetworkTestSave(name='bad',network_ref=reference,input_ref=reference,faults=[{'kind':'udp_drop'},{'kind':'udp_repeat'}])


def test_saved_integrity_and_runtime_changes_are_visible(env):
    from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
    from ade2e import jobs
    db,client,registry,sdk=env;network,source,test=saved(env)
    before=sdk.preview_vecu_network_test(test)
    registry.get('vecu-network').runtime_hash='b'*64
    after=sdk.preview_vecu_network_test(test)
    assert before['preview_hash']!=after['preview_hash']
    assert before['prepared']['input']['test']==after['prepared']['input']['test']
    with jobs._write(db) as session:
        row=session.get(SimulationCatalogRevisionRow,('default','vecu-network-config',network['id'],1))
        value=json.loads(row.manifest);value['can']['command_id']=1;row.manifest=canonical_bytes(value)
    assert client.get('/api/v1/virtual-ecu-network/records/networks/'+network['id'],params={'revision':1}).status_code==409
    with pytest.raises(Exception,match='integrity failed'):sdk.preview_vecu_network_test(test)


def test_can_counter_wrap_bound_covers_adjacent_faults_and_reset_gap():
    reference={'id':'fixture','revision':1,'sha256':'a'*64}
    base=dict(name='CAN loss limit',network_ref=reference,input_ref=reference)
    for faults in [
        [{'kind':'can_command_drop','start_ms':3000,'end_ms':10000}],
        [{'kind':'os_event_drop','start_ms':3000,'end_ms':10000}],
        [{'kind':'can_command_drop','start_ms':3000,'end_ms':6500},{'kind':'os_event_drop','start_ms':6500,'end_ms':10000}],
        [{'kind':'can_command_drop','start_ms':3000,'end_ms':9300},{'kind':'ecu_reset','start_ms':9300,'end_ms':9500}],
        [{'kind':'can_command_drop','start_ms':3000,'end_ms':9350}],
    ]:
        with pytest.raises(ValueError,match='fewer than 127 consecutive missing'):
            NetworkTestSave(**base,faults=faults)
    for faults in [
        [{'kind':'can_command_drop','start_ms':3000,'end_ms':7000}],
        [{'kind':'can_command_drop','start_ms':3000,'end_ms':9300}],
        [{'kind':'can_command_drop','start_ms':3000,'end_ms':6500},{'kind':'os_event_drop','start_ms':6550,'end_ms':10000}],
        [{'kind':'ecu_reset','start_ms':3000,'end_ms':10000}],
        [{'kind':'udp_drop','start_ms':3000,'end_ms':10000}],
    ]:
        assert NetworkTestSave(**base,faults=faults)


@pytest.mark.parametrize('reset_start_ms',[5000,6900])
def test_reset_starting_during_restbus_preserves_sdk_schedule(env,reset_start_ms):
    _,_,_,sdk=env;network,source,_=saved(env)
    faults=[dict(kind='restbus_drop',start_ms=3000,end_ms=7000),dict(kind='ecu_reset',start_ms=reset_start_ms,end_ms=reset_start_ms+200)]
    test=sdk.save_vecu_network_record('tests',dict(name='Restbus with reset',network_ref=ref(network),input_ref=ref(source),faults=faults),idempotency_key='compound')
    assert [(f['kind'],f['start_ms'],f['end_ms']) for f in test['faults']]==[('restbus_drop',3000,7000),('ecu_reset',reset_start_ms,reset_start_ms+200)]
    assert sdk.vecu_network_record('tests',test['id'],revision=1)==test
    assert sdk.preview_vecu_network_test(test)['prepared']['input']['test']['faults']==test['faults']


@pytest.mark.parametrize('faults',[
    [dict(kind='restbus_drop',start_ms=3000,end_ms=7000),dict(kind='ecu_reset',start_ms=6900,end_ms=7200)],
    [dict(kind='ecu_reset',start_ms=2900,end_ms=3100),dict(kind='restbus_drop',start_ms=3000,end_ms=7000)],
    [dict(kind='restbus_drop',start_ms=3000,end_ms=7000),dict(kind='os_event_drop',start_ms=5000,end_ms=5200)],
    [dict(kind='restbus_drop',start_ms=3000,end_ms=7000),dict(kind='ecu_reset',start_ms=5000,end_ms=5500),dict(kind='ecu_reset',start_ms=5200,end_ms=5400)],
    [dict(kind='restbus_drop',start_ms=3000,end_ms=7000),dict(kind='ecu_reset',start_ms=5000,end_ms=5200),dict(kind='restbus_drop',start_ms=5500,end_ms=6500)],
])
def test_unsupported_overlaps_are_rejected_even_when_not_adjacent(faults):
    reference={'id':'fixture','revision':1,'sha256':'a'*64}
    with pytest.raises(ValueError,match='Overlapping faults support only'):
        NetworkTestSave(name='bad overlap',network_ref=reference,input_ref=reference,faults=faults)


def test_unsorted_compound_schedule_is_rejected():
    reference={'id':'fixture','revision':1,'sha256':'a'*64}
    with pytest.raises(ValueError,match='ordered by start_ms'):
        NetworkTestSave(name='bad order',network_ref=reference,input_ref=reference,faults=[dict(kind='ecu_reset',start_ms=5000,end_ms=5200),dict(kind='restbus_drop',start_ms=3000,end_ms=7000)])
