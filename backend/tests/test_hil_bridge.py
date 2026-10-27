import pytest
from fastapi import APIRouter,FastAPI,Depends,Request
from fastapi.testclient import TestClient
from ade2e.database import Database,Settings
from ade2e.experiment_runtime import ExperimentRegistry,ExperimentExecution,ExperimentOutputFile
from ade2e.hil_bridge import register_hil_bridge_routes
from ade2e.vecu import register_vecu_routes,example,ref
from ade2e.vecu_network import register_vecu_network_routes
from ade2e.team_access import register_team_routes,authenticate_request
from axiom_sdk import AxiomClient

@pytest.fixture
def env(tmp_path):
    settings=Settings(data_dir=tmp_path,seed_examples=False,api_token='test',team_mode=False)
    db=Database(settings);db.initialize();registry=ExperimentRegistry([],load_plugins=False)
    def auth(request:Request):authenticate_request(request,db,settings,request.headers.get('authorization','')[7:])
    api=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)]);register_team_routes(api,db);register_vecu_routes(api,db,registry);register_vecu_network_routes(api,db,registry);register_hil_bridge_routes(api,db,registry)
    app=FastAPI();app.include_router(api);app.state.registry=registry;app.state.db=db
    with TestClient(app,headers={'Authorization':'Bearer test'}) as client:
        sdk=AxiomClient('http://testserver',token='test',transport=client._transport)
        net=sdk.save_vecu_network_record('networks',{'name':'n'},idempotency_key='n')
        inp=sdk.save_vecu_record('inputs',example(),idempotency_key='i')
        test=sdk.save_vecu_network_record('tests',dict(name='source',network_ref=ref(net),input_ref=ref(inp),faults=[{'kind':'udp_delay'}]),idempotency_key='t')
        yield sdk,test,client

def test_binding_preserves_source_revision_and_supported_boundaries(env):
    sdk,test,_=env;body=dict(name='HIL',source_ref=ref(test))
    saved=sdk.save_hil_binding(body,idempotency_key='b')
    assert sdk.save_hil_binding(body,idempotency_key='b')==saved
    assert saved['source']['test']==test and saved['injections'][0]['boundary']=='perception.udp.tx'
    assert sdk.hil_binding(saved['id'],revision=1)==saved
    assert sdk.hil_bindings()['items']==[saved]
    newer=sdk.save_hil_binding(body|{'id':saved['id'],'expected_revision':1,'name':'v2'},idempotency_key='b2')
    assert newer['revision']==2 and sdk.hil_binding(saved['id'],revision=1)==saved
    assert sdk.hil_bridge_readiness()['available'] is False
    with pytest.raises(Exception):sdk.preview_hil_binding(saved)
    assert sdk.hil_bridge_executions()['items']==[]

def test_no_hardware_readiness_from_profile_or_vcan(env):
    sdk,test,_=env;profiles=sdk.hil_bridge_profiles()['items']
    assert {p['target'] for p in profiles}=={'vcan','hardware'}
    hardware=sdk.save_hil_binding(dict(name='real',source_ref=ref(test),profile_id='axiom-two-ecu-hardware'),idempotency_key='hardware')
    assert hardware['profile']['roles']['adas']=='physical_ecu'
    with pytest.raises(Exception):sdk.execute_hil_binding(hardware,idempotency_key='blocked')
    assert sdk.hil_bridge_executions()['items']==[]


def test_model_link_needs_completed_pair_in_same_project_and_carla_source(env):
    sdk,source,client=env
    coupling=dict(pair_ref=dict(id='hilmodel_missing',revision=1,sha256='a'*64),baseline_execution='job_a',faulted_execution='job_b')
    for profile in ('axiom-two-ecu-hardware','axiom-two-ecu-vcan'):
        with pytest.raises(Exception,match='CARLA closed loop'):
            sdk.save_hil_binding(dict(name='model',source_ref=ref(source),profile_id=profile,model_coupling=coupling),idempotency_key=profile)
    assert sdk.hil_model_injections()['items']==[]


def test_fractional_model_time_is_preserved_in_authoring_contract(env,monkeypatch):
    # Isolates authoring arithmetic; native sealing/replay is accepted separately.
    import ade2e.hil_bridge as bridge
    import ade2e.hil_model_coupling as coupling
    sdk,source,_=env;original=bridge.source_snapshot
    def snapshot(*args):return original(*args)|dict(mode='carla_closed_loop',faults=[])
    monkeypatch.setattr(bridge,'source_snapshot',snapshot)
    monkeypatch.setattr(coupling,'compile_coupling',lambda *args:{'summary':dict(injection=dict(kind='model_camera_raw_csi',start_ms=3005.,end_ms=3005.03))})
    config=dict(pair_ref=dict(id='hilmodel_fixture',revision=1,sha256='a'*64),baseline_execution='job_a',faulted_execution='job_b')
    saved=sdk.save_hil_binding(dict(name='fractional',source_ref=ref(source),model_coupling=config),idempotency_key='fractional')
    assert saved['injections'][0]['end_ms']==3005.03
    assert saved['source']['test']==source

def test_hash_idempotency_and_project_scope(env):
    sdk,test,client=env
    with pytest.raises(Exception):sdk.save_hil_binding(dict(name='bad',source_ref=ref(test)|{'sha256':'f'*64}),idempotency_key='bad')
    saved=sdk.save_hil_binding(dict(name='ok',source_ref=ref(test)),idempotency_key='b')
    with pytest.raises(Exception):sdk.save_hil_binding(dict(name='different',source_ref=ref(test)),idempotency_key='b')
    other=client.post('/api/v1/projects',json={'name':'other'}).json()['id']
    assert client.get('/api/v1/hil-bridge/bindings/'+saved['id'],params={'project_id':other,'revision':1}).status_code==404
    assert client.get('/api/v1/hil-bridge/executions/missing',params={'project_id':other}).status_code==404

@pytest.mark.parametrize('body',[{'name':'  '},{'max_lateness_ms':float('inf')},{'profile_id':'shell'},{'command':'echo unsafe'}])
def test_invalid_settings_not_saved(env,body):
    sdk,test,_=env
    with pytest.raises(Exception):sdk.save_hil_binding(dict(name='binding',source_ref=ref(test))|body,idempotency_key='invalid')
    assert sdk.hil_bindings()['items']==[]

def test_normal_queue_export_cancel_and_causality_contract(env,tmp_path):
    # Authored handler output verifies queue/sealing/API only, not vcan or ECU execution.
    from axiom_hil_gateway import GatewayHandler
    from ade2e.worker import run_once
    from ade2e.adapters import Registry
    from ade2e.service import canonical_bytes,digest
    from ade2e.experiment_models import ExperimentArtifactRow
    from sqlalchemy import select
    sdk,test,client=env;registry=client.app.state.registry;db=client.app.state.db
    class Fixture(GatewayHandler):
        def probe(self,profile_id):
            if profile_id.endswith('hardware'):raise ValueError('Physical hardware unavailable')
            return dict(available=True,installation_sha256='a'*64,device=dict(target='vcan',codec_id='axiom-example/1'))
        def execute(self,snapshot,work_dir,should_cancel):
            report={'schema_version':'hil-bridge-causality/1','cases':[],'classification':'contract_fixture_only'}
            path=work_dir/'hil-causality.json';path.write_bytes(canonical_bytes(report))
            return ExperimentExecution(summary={'verdict':'fixture_only'},artifacts=[ExperimentOutputFile(path,path.name)],fidelity='contract_fixture_only')
    fixture=Fixture(database=db);fixture.configured=True;registry.register(fixture)
    binding=sdk.save_hil_binding(dict(name='q',source_ref=ref(test)),idempotency_key='qb')
    preview=sdk.preview_hil_binding(binding)
    assert preview['prepared']['input']['binding']['source_ref']==ref(test)
    job=sdk.execute_hil_binding(binding,idempotency_key='j')
    assert sdk.execute_hil_binding(binding,idempotency_key='j')['id']==job['id']
    sdk.cancel_hil_bridge_execution(job['id'])
    assert sdk.hil_bridge_execution(job['id'])['status']=='cancelled'
    import json
    assert json.loads(sdk.export_hil_bridge_execution(job['id']))['execution']['id']==job['id']
    job=sdk.execute_hil_binding(binding,idempotency_key='j2')
    run_once(db,Registry(load_plugins=False),'hil-contract-test',job_id=job['id'],experiment_registry=registry)
    detail=sdk.hil_bridge_execution(job['id'])
    assert detail['status']=='completed'
    assert digest(json.loads(sdk.export_hil_bridge_execution(job['id'])))==detail['manifest_hash']
    report=sdk.hil_bridge_causality(job['id'])
    assert report['manifest_sha256']==detail['manifest_hash'] and report['cases']==[]
    with db.read_session() as session:
        row=session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==job['id']))
        from ade2e.experiment_jobs import artifact_path
        path=artifact_path(db,row)
    path.write_bytes(b'corrupt')
    with pytest.raises(Exception):sdk.hil_bridge_causality(job['id'])

@pytest.mark.parametrize('fault',['ecu_reset','os_event_drop'])
def test_unsupported_internals_fail_before_device_probe_or_queue(env,fault):
    from axiom_hil_gateway import GatewayHandler
    sdk,source,client=env
    test=sdk.save_vecu_network_record('tests',dict(name=fault,network_ref=source['network_ref'],input_ref=source['input_ref'],faults=[dict(kind=fault)]),idempotency_key=fault)
    binding=sdk.save_hil_binding(dict(name=fault,source_ref=ref(test),profile_id='axiom-two-ecu-hardware'),idempotency_key='b-'+fault)
    assert binding['injections'][0]['supported'] is False
    class NeverProbe(GatewayHandler):
        def probe(self,*args):raise AssertionError('Unsupported fault reached device I/O')
    handler=NeverProbe(database=client.app.state.db);handler.configured=True
    client.app.state.registry.register(handler)
    with pytest.raises(Exception,match='driver'):
        sdk.execute_hil_binding(binding,idempotency_key='blocked-'+fault)
    assert sdk.hil_bridge_executions()['items']==[]

def test_feedback_boundary_revision_does_not_mutate_original_test(env):
    sdk,source,_=env
    fault=dict(kind='brake_feedback_drop',start_ms=3000,end_ms=7000)
    binding=sdk.save_hil_binding(dict(name='feedback loss',source_ref=ref(source),additional_faults=[fault]),idempotency_key='feedback')
    assert binding['source']['test']==source
    assert binding['injections'][-1]['boundary']=='brake.plant.feedback'
    assert sdk.vecu_network_record('tests',source['id'],revision=source['revision'])==source

@pytest.mark.parametrize('fault',['ecu_reset','os_event_drop'])
def test_reference_internal_fault_can_be_added_without_mutating_source(env,fault):
    sdk,source,client=env
    saved=sdk.save_hil_binding(dict(name='hook',source_ref=ref(source),additional_faults=[dict(kind=fault,start_ms=3000,end_ms=3200)]),idempotency_key='hook-'+fault)
    assert saved['source']['test']==source
    assert saved['injections'][-1]['supported'] is True
    assert saved['profile']['version']==4
    assert sdk.hil_binding(saved['id'],revision=1)==saved
    with pytest.raises(Exception):
        sdk.save_hil_binding(dict(name='overlap',source_ref=ref(source),additional_faults=[dict(kind=fault,start_ms=3000,end_ms=3200),dict(kind=fault,start_ms=3150,end_ms=3300)]),idempotency_key='overlap-'+fault)

@pytest.mark.parametrize('boundary',['camera_raw_csi','fpga','power','analog'])
def test_native_model_pair_saved_atomically_and_exactly_restored(env,boundary):
    from ade2e.engineering_lab import read_test
    from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow as Row
    sdk,source,client=env
    item=next(x for x in sdk.hil_model_injection_catalog()['items'] if x['boundary']==boundary)
    body=dict(name='injection',settings=item['settings'])
    saved=sdk.save_hil_model_injection(body,idempotency_key='model-'+boundary)
    assert sdk.save_hil_model_injection(body,idempotency_key='model-'+boundary)==saved
    assert sdk.hil_model_injection(saved['id'])==saved
    assert not saved['plan']['carla_connected'] and not saved['plan']['physical_hardware_connected']
    with client.app.state.db.read_session() as session:
        for side,ref in saved['tests'].items():
            test=read_test(session.get(Row,('default','engineering-test',ref['id'],ref['revision'])))
            assert test['sha256']==ref['sha256']
            assert test['injection_origin']['plan_sha256']==saved['plan']['sha256']
            assert test['config']==saved['plan']['sides'][side]
            if boundary in ('camera_raw_csi','fpga'):
                assert (test['config']['width'],test['config']['height'],test['config']['frames'])==(1920,1080,30)
    with pytest.raises(Exception):sdk.save_hil_model_injection(body|{'seed':43},idempotency_key='model-'+boundary)


def test_long_fpga_stall_preserves_full_resolution_and_leaves_native_recovery(env):
    sdk,source,client=env
    body=dict(name='long stall',settings=dict(boundary='fpga',channel=0,at_us=5000,duration_us=300000))
    pair=sdk.save_hil_model_injection(body,idempotency_key='long-stall')
    config=pair['plan']['sides']['faulted']
    assert (config['width'],config['height'],config['frames'],config['fps'],len(config['channels']))==(1920,1080,30,30,4)
    assert config['faults'][0]['duration_cycles']==30000000
    with pytest.raises(Exception):sdk.save_hil_model_injection(body|{'settings':body['settings']|{'at_us':900000}},idempotency_key='no-recovery')


def test_long_power_sag_native_axis_is_not_stretched_and_leaves_recovery(env):
    sdk,source,client=env
    body=dict(name='long power sag',settings=dict(boundary='power',start_ms=3,end_ms=303,stop_ms=400,voltage_v=6))
    pair=sdk.save_hil_model_injection(body,idempotency_key='long-power')
    config=pair['plan']['sides']['faulted']
    assert config['step_s']==1e-5 and config['stop_s']==.4
    assert '0.303 6.0' in config['netlist'] and '0.4 12' in config['netlist']
    assert sdk.hil_model_injection(pair['id'])==pair
    with pytest.raises(Exception):sdk.save_hil_model_injection(body|{'settings':body['settings']|{'stop_ms':300}},idempotency_key='power-no-recovery')


def test_model_set_rejects_ambiguous_single_multi_and_duplicate_pairs():
    from ade2e.hil_bridge_schemas import BindingSave
    config=dict(pair_ref=dict(id='hilmodel_a',revision=1,sha256='a'*64),baseline_execution='job_a',faulted_execution='job_b')
    body=dict(name='dual',source_ref=dict(id='source',revision=1,sha256='b'*64))
    for values in (dict(model_couplings=[config]),dict(model_couplings=[config,config]),dict(model_coupling=config,model_couplings=[config,config])):
        with pytest.raises(ValueError):BindingSave.model_validate(body|values)


def test_model_set_keeps_measurements_policies_and_source_ids_separate(monkeypatch):
    from fastapi import HTTPException
    import ade2e.hil_model_coupling as coupling
    def compile_one(db,session,project,config,duration):
        n=config['boundary']
        return dict(summary=dict(boundary=n,runs={s:dict(execution_id=n+s) for s in ('baseline','faulted')},injection=dict(kind='model_'+n,start_ms=3000,end_ms=3100)),
            policy=dict(boundary=n,min_hold_ms=config['hold']),samples={s:[dict(sequence=i,boundary=n) for i in range(duration//50)] for s in ('baseline','faulted')})
    monkeypatch.setattr(coupling,'compile_coupling',compile_one)
    configs=[dict(boundary='power',hold=500),dict(boundary='fpga',hold=1000)]
    result=coupling.compile_model_set(None,None,'p',configs,100)
    assert result['policy']['combination']=='any_protect'
    assert result['policy']['members']['power']['min_hold_ms']==500
    assert result['policy']['members']['fpga']['min_hold_ms']==1000
    assert result['summary']['runs']['faulted']['members']['fpga']['execution_id']=='fpgafaulted'
    assert set(result['samples']['baseline'][1]['samples'])=={'power','fpga'}
    with pytest.raises(HTTPException,match='One model per measurement boundary'):
        coupling.compile_model_set(None,None,'p',[configs[0],configs[0]],100)

@pytest.mark.parametrize('settings',[
    {'boundary':'power','voltage_v':12},{'boundary':'analog','offset_v':0},
    {'boundary':'power','start_ms':7,'end_ms':3},{'boundary':'fpga','channel':4},
    {'boundary':'camera_raw_csi','at_us':-1},{'boundary':'power','command':'anything'},
])
def test_model_injection_invalid_inputs_fail_closed(env,settings):
    sdk,_,_=env
    with pytest.raises(Exception):sdk.save_hil_model_injection(dict(settings=settings),idempotency_key='invalid-model')


def test_source_reset_restart_contract_preserved_and_long_event_loss_rejected(env):
    sdk,source,_=env
    reset=sdk.save_vecu_network_record('tests',dict(name='original reset window',network_ref=source['network_ref'],input_ref=source['input_ref'],faults=[dict(kind='ecu_reset')]),idempotency_key='reset-window')
    binding=sdk.save_hil_binding(dict(name='reuse reset',source_ref=ref(reset)),idempotency_key='reset-reuse')
    assert binding['source']['test']['faults'][0]['end_ms']==7000
    assert binding['injections'][0]['end_ms']==3200
    assert binding['injections'][0]['source_end_ms']==7000
    with pytest.raises(Exception,match='127'):
        sdk.save_hil_binding(dict(name='too long',source_ref=ref(source),additional_faults=[dict(kind='os_event_drop',start_ms=50,end_ms=6400)]),idempotency_key='too-long')


def test_repeated_fpga_stall_preserves_native_schedule_and_source(env):
    sdk,_,_=env
    body=dict(settings=dict(boundary='fpga',channel=0,at_us=5000,duration_us=100,
        repeats=[dict(at_us=800000,duration_us=100)]))
    pair=sdk.save_hil_model_injection(body,idempotency_key='recurrent-fpga')
    assert sdk.hil_model_injection(pair['id'])==pair
    config=pair['plan']['sides']['faulted']
    assert (config['width'],config['height'],config['frames'],config['fps'],len(config['channels']))==(1920,1080,30,30,4)
    assert [f['at_cycle'] for f in config['faults']]==[500000,80000000]
    assert [f['duration_cycles'] for f in config['faults']]==[10000,10000]
    assert not pair['plan']['sides']['baseline']['faults']
    assert len(config['trace_windows'])==5
    for repeats in ([dict(at_us=5050,duration_us=100)], [dict(at_us=900000,duration_us=60000)],
                    [dict(at_us=800000),dict(at_us=700000)]):
        with pytest.raises(Exception):sdk.save_hil_model_injection(body|{'settings':body['settings']|{'repeats':repeats}},idempotency_key='invalid-repeat')


def test_model_set_keeps_recurrent_injection_markers(monkeypatch):
    import ade2e.hil_model_coupling as coupling
    def one(db,session,project,config,duration):
        n=config['boundary'];fault=dict(kind='model_'+n,start_ms=3005,end_ms=3005.1)
        summary=dict(boundary=n,injection=fault,runs={'baseline':{},'faulted':{}})
        if n=='fpga':summary['injections']=[fault,fault|dict(start_ms=3800,end_ms=3800.1)]
        return dict(summary=summary,policy={},samples={'baseline':[],'faulted':[]})
    monkeypatch.setattr(coupling,'compile_coupling',one)
    result=coupling.compile_model_set(None,None,'p',[{'boundary':'power'},{'boundary':'fpga'}],0)
    assert [v['start_ms'] for v in result['summary']['injections']]==[3005,3005,3800]


def test_empty_recurrence_keeps_original_single_stall_request_identity():
    from ade2e.hil_boundary_models import ModelInjectionSave
    from ade2e.service import digest
    body=ModelInjectionSave(settings=dict(boundary='fpga'))
    original=dict(project_id='default',name='경계 주입 모델 A/B',seed=42,
        settings=dict(boundary='fpga',channel=0,at_us=5000,duration_us=100))
    assert body.model_dump()==original
    assert digest(body.model_dump())==digest(original)


def test_power_recurrence_uses_native_axis_and_preserves_single_sag_identity(env):
    from ade2e.hil_boundary_models import ModelInjectionSave, compile_plan
    from ade2e.service import digest
    sdk,_,_=env
    prior=ModelInjectionSave(settings=dict(boundary='power'))
    assert prior.model_dump()['settings']==dict(start_ms=3,end_ms=7,stop_ms=10,boundary='power',voltage_v=6)
    assert compile_plan(prior)['sides']['faulted']['step_s']==1e-5
    body=dict(settings=dict(boundary='power',start_ms=3,end_ms=303,stop_ms=1750,step_us=20,
        voltage_v=6,repeats=[dict(start_ms=1350,end_ms=1650)]))
    pair=sdk.save_hil_model_injection(body,idempotency_key='power-recurrence')
    assert sdk.hil_model_injection(pair['id'])==pair
    native=pair['plan']['sides']['faulted']
    assert native['stop_s']==1.75 and native['step_s']==2e-5
    assert '1.35 12 1.350001 6.0 1.65 6.0 1.650001 12' in native['netlist']
    assert 'DC 12' in pair['plan']['sides']['baseline']['netlist']
    for updates in (dict(step_us=1),dict(repeats=[dict(start_ms=302,end_ms=500)]),
                    dict(repeats=[dict(start_ms=1350,end_ms=1750)]),
                    dict(repeats=[dict(start_ms=1350,end_ms=1350.001)])):
        with pytest.raises(Exception):sdk.save_hil_model_injection(body|{'settings':body['settings']|updates},idempotency_key='bad-power-repeat')


def test_power_recurrence_after_two_seconds_keeps_twenty_us_and_bounded_budget(env):
    from ade2e.hil_boundary_models import ModelInjectionSave
    sdk,_,_=env
    settings=dict(boundary='power',start_ms=3,end_ms=303,stop_ms=3750,step_us=20,voltage_v=6,
        repeats=[dict(start_ms=1350,end_ms=1650),dict(start_ms=2850,end_ms=3150)])
    pair=sdk.save_hil_model_injection(dict(settings=settings),idempotency_key='late-power-recurrence')
    assert sdk.hil_model_injection(pair['id'])==pair
    normal,faulted=pair['plan']['sides']['baseline'],pair['plan']['sides']['faulted']
    assert normal['stop_s']==faulted['stop_s']==3.75
    assert normal['step_s']==faulted['step_s']==2e-5
    assert '2.85 12 2.850001 6.0 3.15 6.0 3.150001 12' in faulted['netlist']
    assert 'DC 12' in normal['netlist']
    for update in (dict(step_us=10),dict(stop_ms=3150),dict(stop_ms=4001),dict(repeats=[dict(start_ms=2850,end_ms=2849)])):
        with pytest.raises(ValueError):ModelInjectionSave(settings=settings|update)


def test_fpga_recurrence_after_one_second_extends_actual_full_resolution_source(env):
    from ade2e.hil_boundary_models import ModelInjectionSave, compile_plan
    sdk,_,_=env
    settings=dict(boundary='fpga',frames=75,channel=0,at_us=5000,duration_us=100,
        repeats=[dict(at_us=800000,duration_us=200),dict(at_us=2250000,duration_us=200)])
    pair=sdk.save_hil_model_injection(dict(settings=settings),idempotency_key='late-fpga-recurrence')
    assert sdk.hil_model_injection(pair['id'])==pair
    for side,cfg in pair['plan']['sides'].items():
        assert (cfg['width'],cfg['height'],cfg['fps'],cfg['clock_hz'],len(cfg['channels']))==(1920,1080,30,100000000,4)
        assert cfg['frames']==75 and cfg['max_cycles']==260000000
        assert {66,67,68}.issubset(cfg['preview_frames']) and len(cfg['trace_windows'])==7
    assert [f['at_cycle'] for f in pair['plan']['sides']['faulted']['faults']]==[500000,80000000,225000000]
    with pytest.raises(ValueError):ModelInjectionSave(settings=settings|{'frames':30})
    with pytest.raises(ValueError):ModelInjectionSave(settings=settings|{'repeats':[dict(at_us=2499900,duration_us=200)]})
    original=compile_plan(ModelInjectionSave(settings=dict(boundary='fpga')))
    assert 'frames' not in original['settings'] and original['sides']['baseline']['frames']==30
