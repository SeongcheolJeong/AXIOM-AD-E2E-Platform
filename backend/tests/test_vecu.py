import copy,json,hashlib
from pathlib import Path
import pytest
from fastapi import APIRouter,FastAPI,Depends,Request
from fastapi.testclient import TestClient
from ade2e.database import Database,Settings
from ade2e.team_access import authenticate_request,register_team_routes
from ade2e.experiment_routes import register_experiment_routes
from ade2e.experiment_runtime import ExperimentRegistry
from ade2e.adapters import Registry,AdapterCancelled
from ade2e.worker import run_once
from ade2e.vecu import register_vecu_routes,ref,example
from ade2e.vecu_schemas import TestSave as VecuTestSave,InputSave
from axiom_vecu import VecuHandler,Oracle,profile
from axiom_vecu.runtime import Process,catalog
from axiom_sdk import AxiomClient

@pytest.fixture
def env(tmp_path):
    settings=Settings(data_dir=tmp_path,api_token='test',team_mode=False,seed_examples=False)
    db=Database(settings);db.initialize();handler=VecuHandler(db);registry=ExperimentRegistry([handler],load_plugins=False)
    def auth(request:Request):authenticate_request(request,db,settings,request.headers.get('authorization','')[7:])
    router=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
    register_team_routes(router,db);register_vecu_routes(router,db,registry);register_experiment_routes(router,db,registry)
    app=FastAPI();app.include_router(router)
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        yield db,client,registry,AxiomClient('http://testserver',token='test',transport=client._transport)

def saved(env,kind='delay'):
    db,client,registry,sdk=env
    package=sdk.save_vecu_record('packages',dict(name='ECU',runtime_id='reference-traffic-dut'),idempotency_key='package')
    source=sdk.save_vecu_record('inputs',example(),idempotency_key='source')
    test=sdk.save_vecu_record('tests',dict(name='fault',package_ref=ref(package),input_ref=ref(source),faults=[dict(kind=kind,start_step=80,end_step=120)]),idempotency_key='test-'+kind)
    return package,source,test

def execute(env,test,key):
    db,client,registry,sdk=env
    job=sdk.execute_vecu_test(test,idempotency_key=key)
    run_once(db,Registry(load_plugins=False),'vecu-test-worker',job_id=job['id'],experiment_registry=registry)
    result=sdk.experiment_execution(job['id']);assert result['status']=='completed',result
    return result

@pytest.mark.parametrize('kind',['delay','dropout','repeat'])
def test_actual_process_replay_worker_three_repeats_and_export(env,kind):
    _,source,test=saved(env,kind);sdk=env[-1]
    results=[execute(env,test,kind+str(i)) for i in range(3)]
    assert len({json.dumps({k:v['logical_sha256'] for k,v in r['summary']['sides'].items()},sort_keys=True) for r in results})==1
    for result in results:
        s=result['summary'];assert s['verdict']=='passed' and not s['production_software'] and not s['physical_hardware_connected']
        assert s['sides']['baseline']['degraded_steps']==0
        assert s['sides']['faulted']['degraded_steps']>0 and s['sides']['faulted']['last_state']=='tracking'
    run=results[-1]
    for a in run['artifacts']:
        data=env[1].get(a['download_url']).content;assert hashlib.sha256(data).hexdigest()==a['sha256']
    export=env[1].get('/api/v1/experiments/executions/'+run['id']+'/export');assert hashlib.sha256(export.content).hexdigest()==run['manifest_hash']
    assert sdk.vecu_record('tests',test['id'],revision=test['revision'])==test

def test_reference_process_matches_original_python_dut(env,tmp_path):
    from axiom_carla.hil_dut import ReferenceDut
    src=example();params={'max_object_age_ms':250,'recovery_fresh_steps':3}
    init=dict(schema_version='hil-dut-init/1',op='init',route=src['route'],target_speed_mps=3,physics=dict(wheelbase_m=2.7,max_steer_angle_rad=.6),dut_profile=profile(params,'default'))
    dut=ReferenceDut(init);child=Process(catalog()['reference-traffic-dut'],init,tmp_path/'child.log')
    try:
        for i,frame in enumerate(src['frames']):
            p=max(0,i-8) if i>100 else i
            # Keep packet sequence monotonic at the delay transition.
            p=max(100,p) if i>100 else p
            request=dict(schema_version='hil-dut-step/1',op='step',sequence=i,time_s=i*.05,ego=frame['ego'],object_packet=dict(sequence=p,time_s=p*.05,objects=src['frames'][p]['objects']))
            assert child.exchange(request)[0]==dut.step(request)
    finally:child.close()
    assert child.process.poll() is not None

def test_authoring_idempotency_projects_and_runtime_drift(env,monkeypatch):
    package,source,test=saved(env);db,c,reg,sdk=env
    assert c.post('/api/v1/virtual-ecu/packages',json=dict(name='other',runtime_id='reference-traffic-dut'),headers={'Idempotency-Key':'package'}).status_code==409
    assert c.get('/api/v1/virtual-ecu/records/tests/'+test['id']+'?project_id=other&revision=1').status_code==404
    body=dict(name='x',package_ref=ref(package),input_ref=ref(source),project_id='other')
    assert c.post('/api/v1/virtual-ecu/tests',json=body,headers={'Idempotency-Key':'other'}).status_code==404
    current=reg.get('host-vecu').catalog();current['reference-traffic-dut']['runtime_sha256']='0'*64
    monkeypatch.setattr(reg.get('host-vecu'),'catalog',lambda:current)
    with pytest.raises(Exception,match='software changed'):sdk.preview_vecu_test(test)

def test_limits_invalid_refs_and_closed_loop_no_fallback(env):
    package,source,test=saved(env);c=env[1]
    body=dict(name='bad',package_ref=ref(package),input_ref=ref(source),faults=[dict(kind='delay',start_step=20,end_step=999)])
    assert c.post('/api/v1/virtual-ecu/tests',json=body,headers={'Idempotency-Key':'bad'}).status_code==422
    for change in [dict(period_ns=1),dict(frames=[]),dict(route=[{'x':0},{'x':0}])]:
        with pytest.raises(ValueError):InputSave.model_validate(example()|change)
    with pytest.raises(ValueError):VecuTestSave(name='x',package_ref=ref(package),mode='carla_closed_loop',input_ref=ref(source))

def test_age_boundary_and_recovery_independent_oracle():
    params=dict(max_object_age_ms=250,recovery_fresh_steps=3);m=Oracle(params)
    def step(i,p,state,fresh,brake=0):
        req=dict(time_s=i*.05,object_packet=dict(sequence=p,time_s=p*.05))
        rep=dict(object_sequence=p,object_age_s=(i-p)*.05,state=state,fresh_recovery_steps=fresh,control=dict(throttle=0,brake=brake,steer=0))
        m.check(req,rep)
    step(5,0,'tracking',0);step(6,0,'degraded',0,1);step(7,7,'recovering',1,1);step(8,8,'recovering',2,1);step(9,9,'tracking',3)
    with pytest.raises(ValueError):step(16,9,'tracking',0)

def test_cancellation_reaps_child(tmp_path):
    src=example();calls=[0]
    def cancel():
        calls[0]+=1
        if calls[0]>2:raise AdapterCancelled('test cancel')
    init=dict(op='init',schema_version='hil-dut-init/1',route=src['route'],target_speed_mps=3,physics=dict(wheelbase_m=2.7,max_steer_angle_rad=.6))
    child=Process(catalog()['reference-traffic-dut'],init,tmp_path/'cancel.log')
    child.check=cancel
    try:
        cancel();cancel()
        with pytest.raises(AdapterCancelled):child.exchange(dict(op='step'))
    finally:child.close(False)
    assert child.process.poll() is not None

@pytest.mark.parametrize('behavior,exception',[('timeout',TimeoutError),('exit',RuntimeError),('invalid',ValueError)])
def test_actual_child_failure_never_returns_valid_result(tmp_path,behavior,exception):
    import sys
    path=tmp_path/'bad.py'
    path.write_text('''import sys,json,os,time
json.loads(sys.stdin.readline())
print(json.dumps(dict(schema_version='hil-dut-reply/1',op='init',status='ready',pid=os.getpid(),dut_version='fixture')),flush=True)
json.loads(sys.stdin.readline())
behavior='''+repr(behavior)+'''
if behavior=='timeout':time.sleep(30)
elif behavior=='invalid':print('{"schema_version":"wrong"}',flush=True)
''')
    child=Process(dict(command=[sys.executable,str(path)],dut_version='fixture'),dict(op='init'),tmp_path/'bad.log',timeout_s=.2)
    try:
        with pytest.raises(exception):child.exchange(dict(op='step',sequence=0))
    finally:child.close(False)
    assert child.process.poll() is not None

def test_saved_revision_edit_conflict_and_cancel_before_dispatch(env):
    _,_,t=saved(env);sdk=env[-1]
    body={k:t[k] for k in VecuTestSave.model_fields if k in t};body.update(id=t['id'],expected_revision=1,name='new revision')
    new=sdk.save_vecu_record('tests',body,idempotency_key='revision');assert new['revision']==2
    assert sdk.vecu_record('tests',t['id'],revision=1)==t
    with pytest.raises(Exception,match='Revision changed'):sdk.save_vecu_record('tests',body,idempotency_key='stale')
    j=sdk.execute_vecu_test(new,idempotency_key='cancel-job');sdk.cancel_experiment(j['id'])
    result=sdk.experiment_execution(j['id']);assert result['status']=='cancelled' and not result['artifacts']

def test_settings_comparison_uses_same_input_packets(env):
    _,_,t=saved(env);sdk=env[-1]
    body={k:t[k] for k in VecuTestSave.model_fields if k in t};body.update(id=None,comparison='settings_ab',variant={'max_object_age_ms':100,'recovery_fresh_steps':5})
    new=sdk.save_vecu_record('tests',body,idempotency_key='settings')
    result=execute(env,new,'settings-run');s=result['summary']['sides']
    assert s['baseline']['input_sha256']==s['faulted']['input_sha256']
    assert s['baseline']['first_degraded_step']!=s['faulted']['first_degraded_step']

def test_build_api_queues_once_and_scopes_history_without_native_compiler(env,monkeypatch):
    """Queue/API contract only: the native compiler is exercised by adapter tests."""
    db,client,registry,sdk=env;handler=registry.get('host-vecu')
    prepare=handler.prepare
    monkeypatch.setattr(handler,'prepare',lambda spec: {'schema_version':'fixture-build/1','source_id':spec.input['build_source_id']}
        if 'build_source_id' in spec.input else prepare(spec))
    source=dict(id='cpp-immediate',name='C++ immediate fixture')
    monkeypatch.setattr(handler,'build_sources',lambda:[source],raising=False)
    assert sdk.vecu_catalog()['build_sources']==[source]
    job=sdk.build_vecu_package(name='C++ A',source_id='cpp-immediate',idempotency_key='compile')
    assert job['status']=='queued' and job['experiment_kind']=='virtual_ecu'
    assert sdk.build_vecu_package(name='C++ A',source_id='cpp-immediate',idempotency_key='compile')['id']==job['id']
    with pytest.raises(Exception,match='different experiment'):
        sdk.build_vecu_package(name='C++ B',source_id='cpp-ramp',idempotency_key='compile')
    assert sdk.vecu_build(job['id'])['id']==job['id']
    assert sdk.vecu_builds()['items'][0]['source_id']=='cpp-immediate'
    assert sdk.vecu_executions()['items']==[]
    other=client.post('/api/v1/projects',json={'name':'Other build project'}).json()['id']
    assert client.get('/api/v1/virtual-ecu/builds/'+job['id'],params={'project_id':other}).status_code==404
    assert sdk.vecu_builds(project_id=other)['items']==[]
    sdk.cancel_experiment(job['id'])
    assert sdk.vecu_build(job['id'])['status']=='cancelled'
    assert sdk.vecu_builds()['items'][0]['manifest_hash']
    for body in [dict(name='bad',source_id='../../binary'),dict(name='bad',source_id='cpp-immediate',command='cc arbitrary.c')]:
        assert client.post('/api/v1/virtual-ecu/builds',json=body,headers={'Idempotency-Key':'invalid'}).status_code==422

def test_built_package_registration_and_second_package_refs_are_immutable(env,monkeypatch):
    db,client,registry,sdk=env;handler=registry.get('host-vecu');calls=[]
    def built_runtime(identity,project_id,session=None):
        calls.append((identity,project_id,session is not None))
        if identity!='job_compiled':raise ValueError('Build is not completed')
        return dict(kind='compiled_reference',source_id='cpp-ramp',runtime_sha256='b'*64)
    monkeypatch.setattr(handler,'built_runtime',built_runtime,raising=False)
    body=dict(name='built B',build_execution_id='job_compiled')
    package=sdk.save_vecu_record('packages',body,idempotency_key='built-B')
    assert package['runtime_id'] is None and package['runtime']['kind']=='compiled_reference'
    assert calls==[('job_compiled','default',True)]
    assert sdk.save_vecu_record('packages',body,idempotency_key='built-B')==package
    assert len(calls)==1
    for change in [dict(build_execution_id='job_pending'),dict(runtime_id='reference-traffic-dut'),dict(build_execution_id=None),dict(command=['arbitrary'])]:
        assert client.post('/api/v1/virtual-ecu/packages',json=body|change,headers={'Idempotency-Key':'bad-built-'+str(change)}).status_code==422
    a,source,_=saved(env)
    spec=dict(name='Packages',package_ref=ref(a),variant_package_ref=ref(package),input_ref=ref(source),comparison='package_ab',fault_enabled=False)
    test=sdk.save_vecu_record('tests',spec,idempotency_key='packages')
    assert test['variant_package_ref']==ref(package) and test['fault_enabled'] is False
    assert sdk.vecu_record('tests',test['id'],revision=1)==test
    invalid=spec|{'variant_package_ref':ref(package)|{'sha256':'a'*64}}
    assert client.post('/api/v1/virtual-ecu/tests',json=invalid,headers={'Idempotency-Key':'invalid-B'}).status_code==409
    invalid=spec|{'variant_package_ref':ref(package)|{'id':'missing_package'}}
    assert client.post('/api/v1/virtual-ecu/tests',json=invalid,headers={'Idempotency-Key':'missing-B'}).status_code==404

def test_package_comparison_schema_preserves_legacy_carla_contract():
    reference=dict(id='vecu_fixture',revision=1,sha256='a'*64)
    base=dict(name='two builds',package_ref=reference,variant_package_ref=reference,comparison='package_ab')
    replay=VecuTestSave(**base,input_ref=reference,fault_enabled=False)
    assert replay.fault_enabled is False
    closed=VecuTestSave(**base,mode='carla_closed_loop',hil_test_ref=reference,
        baseline=dict(max_object_age_ms=300,recovery_fresh_steps=4))
    assert closed.baseline.max_object_age_ms==300
    for change in [dict(variant_package_ref=None),dict(comparison='fault_ab'),dict(comparison='settings_ab')]:
        with pytest.raises(ValueError):VecuTestSave(**(base|change),input_ref=reference)
    with pytest.raises(ValueError):
        VecuTestSave(name='legacy',package_ref=reference,input_ref=reference,fault_enabled=False)
    with pytest.raises(ValueError):
        VecuTestSave(name='legacy',package_ref=reference,mode='carla_closed_loop',hil_test_ref=reference,
            baseline=dict(max_object_age_ms=300,recovery_fresh_steps=4))
    with pytest.raises(ValueError):
        VecuTestSave(**base,mode='carla_closed_loop',hil_test_ref=reference,
            faults=[dict(kind='repeat',start_step=60,end_step=140)])

def test_project_tokens_can_read_but_only_engineers_can_build(env,monkeypatch):
    db,client,registry,sdk=env;handler=registry.get('host-vecu')
    monkeypatch.setattr(handler,'prepare',lambda spec: {'schema_version':'fixture-build/1','source_id':spec.input['build_source_id']})
    def headers(role):
        token=client.post('/api/v1/projects/default/tokens',json={'user_label':'vECU '+role,'role':role}).json()['token']
        return {'Authorization':'Bearer '+token,'Idempotency-Key':'role-build-'+role}
    viewer=headers('Viewer');engineer=headers('Engineer')
    assert client.get('/api/v1/virtual-ecu/catalog',headers=viewer).status_code==200
    assert client.get('/api/v1/virtual-ecu/builds',headers=viewer).status_code==200
    body=dict(name='permission fixture',source_id='cpp-immediate')
    assert client.post('/api/v1/virtual-ecu/builds',json=body,headers=viewer).status_code==403
    assert client.post('/api/v1/virtual-ecu/packages',json=dict(name='forbidden',runtime_id='reference-traffic-dut'),headers=viewer).status_code==403
    built=client.post('/api/v1/virtual-ecu/builds',json=body,headers=engineer)
    assert built.status_code==202
    assert client.get('/api/v1/virtual-ecu/builds/'+built.json()['id'],headers=viewer).status_code==200
    other=client.post('/api/v1/projects',json={'name':'Private build project'}).json()['id']
    assert client.get('/api/v1/virtual-ecu/builds',params={'project_id':other},headers=viewer).status_code==404
    assert client.post('/api/v1/virtual-ecu/builds',json=body|{'project_id':other},headers=engineer).status_code==404

def test_package_registration_requires_sealed_same_project_untampered_build(env,monkeypatch):
    """Exercise published artifact integrity; compilation is an explicit fixture."""
    from axiom_vecu import builds
    from ade2e.experiment_runtime import ExperimentExecution,ExperimentOutputFile
    from ade2e.service import canonical_bytes,digest
    from ade2e.experiment_models import ExperimentArtifactRow
    from sqlalchemy import select
    db,client,registry,sdk=env;handler=registry.get('host-vecu')
    runtime=dict(kind='compiled_reference',build_key='1'*32,source_id='cpp-immediate',policy_id='immediate',
        dut_version='reference-traffic-cpp/1',protocol='hil-dut/1',binary_sha256='a'*64,source_sha256='b'*64,
        compiler='fixture compiler',target_platform='fixture linux',classification='reference_application',production_software=False)
    runtime['runtime_sha256']=digest(runtime)
    summary=dict(schema_version='vecu-build-result/1',source_id='cpp-immediate',runtime=runtime,verdict='passed')
    def fixture_execute(snapshot,work_dir,should_cancel):
        path=Path(work_dir)/'vecu-build.json';path.write_bytes(canonical_bytes(summary))
        return ExperimentExecution(summary=summary,artifacts=[ExperimentOutputFile(path,'vecu-build.json','application/json')],fidelity='fixture_build_contract_only')
    monkeypatch.setattr(handler,'prepare',lambda spec: {'schema_version':'fixture-build/1','source_id':spec.input['build_source_id']})
    monkeypatch.setattr(handler,'execute',fixture_execute)
    monkeypatch.setattr(handler,'built_runtime',lambda identity,project_id,session=None:builds.built_runtime(db,identity,project_id,session),raising=False)
    build=sdk.build_vecu_package(name='sealed fixture',source_id='cpp-immediate',idempotency_key='sealed-build')
    body=dict(name='registered fixture',build_execution_id=build['id'])
    assert client.post('/api/v1/virtual-ecu/packages',json=body,headers={'Idempotency-Key':'pending'}).status_code==422
    run_once(db,Registry(load_plugins=False),'build-contract-worker',job_id=build['id'],experiment_registry=registry)
    assert sdk.vecu_build(build['id'])['status']=='completed'
    package=sdk.save_vecu_record('packages',body,idempotency_key='sealed-package')
    assert package['runtime']==runtime
    other=client.post('/api/v1/projects',json={'name':'Other build boundary'}).json()['id']
    assert client.post('/api/v1/virtual-ecu/packages',json=body|{'project_id':other},headers={'Idempotency-Key':'other-package'}).status_code==422
    with db.read_session() as session:
        artifact=session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==build['id']))
        path=db.experiment_dir/'artifacts'/artifact.storage_key
    path.write_text('{"tampered":true}')
    assert client.post('/api/v1/virtual-ecu/packages',json=body,headers={'Idempotency-Key':'tampered-package'}).status_code==409

@pytest.mark.parametrize('compiled_side',['baseline','variant','neither'])
def test_carla_package_ab_rejects_mixed_or_uncompiled_packages_before_native_prepare(env,monkeypatch,compiled_side):
    from ade2e import hil_beta
    db,client,registry,sdk=env;handler=registry.get('host-vecu')
    package,_,_=saved(env)
    monkeypatch.setattr(handler,'built_runtime',lambda *args,**kwargs:dict(kind='compiled_reference',source_id='cpp-ramp',runtime_sha256='b'*64))
    compiled=sdk.save_vecu_record('packages',dict(name='compiled fixture',build_execution_id='job_fixture'),idempotency_key='compiled-fixture')
    hil=dict(id='hiltest_fixture',revision=1,sha256='c'*64)
    spec=dict(project_id='default',name='HIL fixture',kind='hil_demo',adapter_id='carla-software-dut',
        comparison_kind='same_scene_fault_ab',input={},seed=42)
    monkeypatch.setattr(hil_beta,'_test',lambda *args,**kwargs:{'experiment':spec})
    monkeypatch.setattr(handler,'check_package',lambda *args:None)
    monkeypatch.setattr(handler.carla,'prepare',lambda *args:pytest.fail('Unsupported package must fail before native CARLA preparation'))
    test=sdk.save_vecu_record('tests',dict(name='unsupported mix',mode='carla_closed_loop',comparison='package_ab',hil_test_ref=hil,
        package_ref=ref(compiled if compiled_side=='baseline' else package),
        variant_package_ref=ref(compiled if compiled_side=='variant' else package)),idempotency_key='mixed-'+compiled_side)
    with pytest.raises(Exception,match='requires two compiled packages'):sdk.preview_vecu_test(test)
