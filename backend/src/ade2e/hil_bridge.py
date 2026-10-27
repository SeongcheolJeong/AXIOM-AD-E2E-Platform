"""HIL binding authoring and immutable executions; no hardware imports in core."""
import json,math
from fastapi import Header,HTTPException,Query,Request,Response
from sqlalchemy import select,func
from . import jobs,experiment_jobs,vecu,vecu_network
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .experiment_models import ExperimentRow,ExperimentExecutionRow,ExperimentArtifactRow
from .job_models import JobRow
from .experiment_schemas import ExperimentCreate
from .hil_bridge_schemas import BindingSave,profiles,get_profile,BOUNDARIES,supports
from .vecu_schemas import Action
from .team_access import project_authorize
from .service import canonical_bytes,digest,uid,utcnow

ADAPTER='hil-gateway';KIND='hil-binding'
ref=vecu.ref

def source_snapshot(session,project,kind,reference):
    if kind=='vecu-network-test':
        test=vecu_network.resolve(session,project,kind,reference)
        network=vecu_network.resolve(session,project,'vecu-network-config',test['network_ref'])
        faults=test['faults'];mode=test['mode'];source=None
        if mode=='fixed_replay':source=vecu.resolve(session,project,'vecu-input',test['input_ref'])
        experiment=test.get('hil_experiment')
    else:
        network=None;source=None
        if kind=='vecu-test':
            test=vecu.resolve(session,project,kind,reference)
            if test['comparison']!='fault_ab':raise HTTPException(422,'HIL reuse currently supports fault A/B, not settings/package replacement')
            expected={'max_object_age_ms':250,'recovery_fresh_steps':3}
            if test['baseline']!=expected or test['variant']!=expected:raise HTTPException(422,'Example HIL ECU uses 250ms/3-packet policy; a matching device profile is required for other policies')
            package=vecu.resolve(session,project,'vecu-package',test['package_ref'])
            if package.get('runtime_id')!='reference-traffic-dut' and package.get('runtime',{}).get('policy_id')!='immediate':raise HTTPException(422,'Selected ECU package needs a matching HIL device profile')
            mode=test['mode'];experiment=test.get('hil_experiment')
            if mode=='fixed_replay':source=vecu.resolve(session,project,'vecu-input',test['input_ref'])
            faults=[dict(kind={'delay':'udp_delay','dropout':'udp_drop','repeat':'udp_repeat'}[f['kind']],
                start_ms=f['start_step']*50,end_ms=f['end_step']*50,delay_ms=f['delay_steps']*50) for f in test['faults']]
        else:
            from .hil_beta import _test
            test=_test(session,project,reference);experiment=test['experiment'];mode='carla_closed_loop';faults=[]
            if experiment['comparison_kind']!='same_scene_fault_ab':raise HTTPException(422,'DUT profile comparison requires a matching HIL device profile')
        if mode=='carla_closed_loop':
            f=experiment['input']['fault']
            faults=[dict(kind={'delay':'udp_delay','drop':'udp_drop','repeat':'udp_repeat'}[f['kind']],
                start_ms=round(f['start_s']*1000),end_ms=round((f['start_s']+f['duration_s'])*1000),delay_ms=f['delay_ms'])]
    return dict(test=test,network=network,source=source,mode=mode,carla_experiment=experiment,
        seed=test.get('seed',experiment['seed'] if experiment else 42),faults=faults,
        duration_ms=len(source['frames'])*50 if source else 18000,
        reuse='unchanged_network_test' if kind=='vecu-network-test' else 'object_test_with_added_CAN_and_two_ECU_boundaries')

def read(row):
    if row is None:raise HTTPException(404,'HIL binding revision not found in project')
    value=json.loads(row.manifest)
    if (digest(value)!=row.manifest_hash or (value.get('id'),value.get('revision'),value.get('project_id'),value.get('schema_version'))
        !=(row.id,row.revision,row.project_id,'hil-binding/1')):raise HTTPException(409,'Binding integrity failed')
    BindingSave.model_validate({k:value[k] for k in BindingSave.model_fields if k in value and k not in ('id','expected_revision')})
    return value|{'sha256':row.manifest_hash}

def resolve(session,project,reference):
    if hasattr(reference,'model_dump'):reference=reference.model_dump()
    value=read(session.get(Row,(project,KIND,reference['id'],reference['revision'])))
    if value['sha256']!=reference['sha256']:raise HTTPException(409,'Binding hash differs')
    return value

def register_hil_bridge_routes(api,db,registry):
    from .hil_boundary_models import register_model_injection_routes
    register_model_injection_routes(api,db)
    base='/hil-bridge'
    def handler():
        value=registry.get(ADAPTER)
        if value is None:raise HTTPException(422,'Optional hil-gateway adapter is not installed')
        return value
    @api.get(base+'/profiles',tags=['hil-bridge'])
    def catalog(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        return dict(schema_version='hil-bridge-catalog/1',items=profiles(),boundaries=BOUNDARIES,
            authoring='available',readiness='query_live_readiness',physical_hil='not_verified')
    @api.get(base+'/readiness',tags=['hil-bridge'])
    def readiness(request:Request,profile_id:str='axiom-two-ecu-vcan',project_id:str='default'):
        project_authorize(request,db,project_id)
        if not get_profile(profile_id):raise HTTPException(422,'Unknown profile')
        try:return handler().readiness(profile_id)
        except (ValueError,OSError,HTTPException) as exc:return dict(available=False,status='not_configured',reason=str(getattr(exc,'detail',exc)))
    @api.post(base+'/bindings',status_code=201,tags=['hil-bridge'])
    def save(body:BindingSave,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer');experiment_jobs._key(body.project_id,idempotency_key,KIND)
        request_hash=digest(body.model_dump(mode='json'));write_id=digest([KIND,idempotency_key])
        with jobs._write(db) as session:
            old=session.get(Row,(body.project_id,'hil-binding-write',write_id,1))
            if old:
                receipt=json.loads(old.manifest)
                if digest(receipt)!=old.manifest_hash or receipt['request_hash']!=request_hash:raise HTTPException(409,'Idempotency key used for another request')
                return resolve(session,body.project_id,receipt['ref'])
            source=source_snapshot(session,body.project_id,body.source_kind,body.source_ref)
            identity=body.id or uid('hilbind')
            latest=session.scalar(select(func.max(Row.revision)).where(Row.project_id==body.project_id,Row.kind==KIND,Row.id==identity)) or 0
            if latest!=body.expected_revision:raise HTTPException(409,'Revision changed')
            faults=source['faults']+[f.model_dump() for f in body.additional_faults]
            model_link=None
            if body.model_coupling or body.model_couplings:
                if body.profile_id!='axiom-two-ecu-vcan' or source['mode']!='carla_closed_loop':raise HTTPException(422,'Model coupling requires reference vCAN and CARLA closed loop')
                if faults:raise HTTPException(422,'Select a fault-free source; transport/internal faults cannot be mixed with model replay')
                from .hil_model_coupling import compile_coupling,compile_model_set
                model_link=(compile_model_set(db,session,body.project_id,body.model_couplings,source['duration_ms']) if body.model_couplings else
                    compile_coupling(db,session,body.project_id,body.model_coupling,source['duration_ms']))['summary']
                faults=model_link.get('injections',[model_link.get('injection')])
            # Network Lab reset is a 200ms restart inside an authored observation
            # window. Preserve that contract rather than holding reset for 3-7s.
            faults=[dict(f,source_end_ms=f['end_ms'],end_ms=f['start_ms']+f['reset_restart_ms'])
                if f['kind']=='ecu_reset' and 'reset_restart_ms' in f else f for f in faults]
            if any(f['end_ms']>=source['duration_ms'] for f in faults):raise HTTPException(422,'Fault must leave a recovery window')
            for i,f in enumerate(faults):
                if any(f['kind']==g['kind'] and f['start_ms']<g['end_ms'] and g['start_ms']<f['end_ms'] for g in faults[:i]):
                    raise HTTPException(422,'Overlapping injections of the same kind are not supported')
            missing=0
            for tick in range(0,math.ceil(max((f['end_ms'] for f in faults),default=0))+50,50):
                missing=missing+1 if any(f['kind'] in ('ecu_reset','os_event_drop','can_command_drop') and f['start_ms']<=tick<f['end_ms'] for f in faults) else 0
                if missing>=127:raise HTTPException(422,'CAN counter recovery requires fewer than 127 consecutive missing commands')
            value=body.model_dump(mode='json',exclude={'id','expected_revision'})|dict(id=identity,revision=latest+1,
                schema_version='hil-binding/1',created_at=utcnow(),profile=get_profile(body.profile_id),source=source,model_link=model_link,
                injections=[dict(f,boundary=BOUNDARIES[f['kind']],supported=supports(get_profile(body.profile_id),f['kind'])) for f in faults])
            result=value|{'sha256':digest(value)};receipt=dict(request_hash=request_hash,ref=ref(result))
            for kind,key,rev,data in [(KIND,identity,latest+1,value),('hil-binding-write',write_id,1,receipt)]:
                session.add(Row(project_id=body.project_id,kind=kind,id=key,revision=rev,manifest=canonical_bytes(data),manifest_hash=digest(data)))
            return result
    @api.get(base+'/bindings',tags=['hil-bridge'])
    def listing(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:return {'items':[read(r) for r in session.scalars(select(Row).where(Row.project_id==project_id,Row.kind==KIND).order_by(Row.id,Row.revision.desc()))]}
    @api.get(base+'/bindings/{identity}',tags=['hil-bridge'])
    def record(identity:str,request:Request,revision:int=Query(ge=1),project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:return read(session.get(Row,(project_id,KIND,identity,revision)))
    def spec_for(identity,body):
        reference=dict(id=identity,revision=body.revision,sha256=body.sha256)
        with db.read_session() as session:value=resolve(session,body.project_id,reference)
        return ExperimentCreate(project_id=body.project_id,name=value['name'],kind='virtual_ecu',adapter_id=ADAPTER,
            input={'binding_ref':reference},seed=value['source']['seed'])
    @api.post(base+'/bindings/{identity}/preview',tags=['hil-bridge'])
    def preview(identity:str,body:Action,request:Request):
        project_authorize(request,db,body.project_id,'Engineer');p=experiment_jobs.prepare(registry,spec_for(identity,body))
        return dict(preview_hash=p.input_hash,prepared=p.to_payload(),worker_readiness='not_verified')
    @api.post(base+'/bindings/{identity}/executions',status_code=202,tags=['hil-bridge'])
    def execute(identity:str,body:Action,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer');experiment_jobs._key(body.project_id,idempotency_key,'hil-bridge-execution')
        key='hil-bridge:'+digest(idempotency_key);e=experiment_jobs.create(db,registry,spec_for(identity,body),key)
        return experiment_jobs.submit(db,registry,e['id'],e['preview_hash'],key)
    def job(session,identity,project):
        value=session.scalar(select(JobRow).join(ExperimentExecutionRow,ExperimentExecutionRow.job_id==JobRow.id).where(JobRow.id==identity,
            JobRow.adapter_id==ADAPTER,ExperimentExecutionRow.project_id==project))
        if value is None:raise HTTPException(404,'HIL execution not found in project')
        return value
    @api.get(base+'/executions',tags=['hil-bridge'])
    def executions(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            rows=session.execute(select(ExperimentRow,JobRow).join(ExperimentExecutionRow,ExperimentExecutionRow.experiment_id==ExperimentRow.id).join(JobRow,JobRow.id==ExperimentExecutionRow.job_id).where(ExperimentRow.project_id==project_id,JobRow.adapter_id==ADAPTER).order_by(JobRow.created_at.desc()).limit(100))
            return {'items':[dict(id=j.id,name=e.name,status=j.status,binding_ref=experiment_jobs.experiment_get(session,e.id)[1]['prepared']['spec']['input']['binding_ref']) for e,j in rows]}
    @api.get(base+'/executions/{identity}',tags=['hil-bridge'])
    def execution(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:return experiment_jobs.detail(session,job(session,identity,project_id))
    @api.post(base+'/executions/{identity}/cancel',tags=['hil-bridge'])
    def cancel(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id,'Engineer')
        with db.read_session() as session:job(session,identity,project_id)
        return jobs.cancel_job(db,identity,kind='experiment')
    @api.get(base+'/executions/{identity}/export',tags=['hil-bridge'])
    def export(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            experiment_jobs.detail(session,job(session,identity,project_id))
            value=session.get(ExperimentExecutionRow,identity)
            if value.manifest is None:raise HTTPException(409,'Terminal immutable receipt required')
            return Response(value.manifest,media_type='application/json',headers={'X-Content-SHA256':value.manifest_hash})
    @api.get(base+'/executions/{identity}/causality',tags=['hil-bridge'])
    def causality(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            detail=experiment_jobs.detail(session,job(session,identity,project_id))
            if detail.status!='completed':raise HTTPException(409,'Completed retained execution required')
            row=session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==identity,ExperimentArtifactRow.name=='hil-causality.json'))
            if row is None:raise HTTPException(409,'Causality artifact missing')
            report=json.loads(experiment_jobs.artifact_path(db,row).read_bytes())
            return report|{'execution_id':identity,'manifest_sha256':detail.manifest_hash,'artifact_sha256':row.sha256}
