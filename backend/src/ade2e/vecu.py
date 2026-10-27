"""Project-scoped virtual ECU packages, inputs and tests on the immutable queue."""
import json
import random
from fastapi import Header, HTTPException, Query, Request
from sqlalchemy import select,func
from . import experiment_jobs,jobs
from .service import canonical_bytes,digest,uid,utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .experiment_models import ExperimentRow,ExperimentExecutionRow
from .job_models import JobRow
from .experiment_schemas import ExperimentCreate
from .team_access import project_authorize
from .vecu_schemas import PackageSave,InputSave,TestSave,Action,BuildCreate

ADAPTER='host-vecu'
MODELS={'vecu-package':PackageSave,'vecu-input':InputSave,'vecu-test':TestSave}

def ref(value):return {k:value[k] for k in ('id','revision','sha256')}

def read(row):
    if row is None:raise HTTPException(404,'vECU revision not found in project')
    try:
        value=json.loads(row.manifest)
        if digest(value)!=row.manifest_hash or (value['project_id'],value['id'],value['revision'],value['schema_version'])!=(row.project_id,row.id,row.revision,row.kind+'/1'):
            raise ValueError('Identity mismatch')
        model=MODELS[row.kind]
        model.model_validate({k:value[k] for k in model.model_fields if k not in ('expected_revision','id') and k in value})
    except (ValueError,KeyError,TypeError) as e:raise HTTPException(409,'vECU saved revision integrity failed') from e
    return value|{'sha256':row.manifest_hash}

def resolve(session,project,kind,reference):
    if hasattr(reference,'model_dump'):reference=reference.model_dump()
    value=read(session.get(Row,(project,kind,reference['id'],reference['revision'])))
    if value['sha256']!=reference['sha256']:raise HTTPException(409,'vECU revision hash differs')
    return value

def example(seed=42):
    offset=random.Random(seed).uniform(-.25,.25)
    def actor(identity,x,speed):return dict(id=identity,x=x,y=0,z=0,heading_rad=0,speed_mps=speed)
    return InputSave(name='18 s · fixed object replay (synthetic)',seed=seed,
        route=[dict(x=0),dict(x=80)],frames=[dict(ego=actor('ego',i*.05*3,3),
            objects=[actor('lead',12+offset+min(i*.05,4)*2,2 if i<80 else 0)]) for i in range(360)]).model_dump()

def register_vecu_routes(api,db,registry):
    base='/virtual-ecu'
    def handler():
        value=registry.get(ADAPTER)
        if value is None:raise HTTPException(422,'Optional host vECU adapter is not installed')
        return value
    def current_runtime(identity):
        try:
            runtime=handler().catalog().get(identity)
            if runtime is None:raise ValueError('Runtime is not operator-registered')
            return runtime
        except (ValueError,OSError) as e:raise HTTPException(422,str(e)) from e
    @api.get(base+'/catalog',tags=['virtual-ecu'])
    def catalog(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        try:
            runtimes=list(handler().catalog().values());error=None
        except (HTTPException,ValueError,OSError) as e:runtimes=[];error=str(getattr(e,'detail',e))
        try:build_sources=handler().build_sources();build_error=None
        except (HTTPException,ValueError,OSError,AttributeError) as e:build_sources=[];build_error=str(getattr(e,'detail',e))
        carla=registry.get('carla-software-dut')
        return dict(schema_version='vecu-catalog/1',runtimes=runtimes,error=error,build_sources=build_sources,build_error=build_error,
            fixed_replay='available' if runtimes else 'not_configured',
            carla=dict(adapter_status=carla.metadata().status if carla else 'not_configured',worker_readiness='not_verified'),
            physical_hil='not_configured',silver='not_connected',sil_kit='not_connected',fmi='not_connected',
            classification='Reference application; not production Level 1 or Qualcomm emulation',input_example=example())
    @api.post(base+'/builds',status_code=202,tags=['virtual-ecu'])
    def build_create(body:BuildCreate,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer')
        # Reuse the durable, immutable execution lifecycle; retries recover the
        # same experiment and submission even if a response was interrupted.
        experiment_jobs._key(body.project_id,idempotency_key,'vecu-build')
        key='vecu-build:'+idempotency_key
        if len(key)>200:key='vecu-build:'+digest(idempotency_key)
        spec=ExperimentCreate(project_id=body.project_id,name=body.name,kind='virtual_ecu',adapter_id=ADAPTER,
            input={'build_source_id':body.source_id},seed=42)
        experiment=experiment_jobs.create(db,registry,spec,key)
        return experiment_jobs.submit(db,registry,experiment['id'],experiment['preview_hash'],key)
    @api.get(base+'/builds',tags=['virtual-ecu'])
    def builds(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            rows=session.execute(select(ExperimentRow,ExperimentExecutionRow,JobRow).join(
                ExperimentExecutionRow,ExperimentExecutionRow.experiment_id==ExperimentRow.id).join(
                JobRow,JobRow.id==ExperimentExecutionRow.job_id).where(ExperimentRow.project_id==project_id,
                ExperimentRow.kind=='virtual_ecu',JobRow.adapter_id==ADAPTER,
                JobRow.prepared['spec']['input']['build_source_id'].as_string().is_not(None))
                .order_by(JobRow.created_at.desc()).limit(100)).all()
            result=[]
            for experiment,execution,job in rows:
                prepared=experiment_jobs.experiment_get(session,experiment.id)[1]['prepared']
                detail=experiment_jobs.detail(session,job)
                result.append(dict(id=job.id,status=job.status,name=experiment.name,source_id=prepared['spec']['input']['build_source_id'],
                    summary=detail.summary,created_at=job.created_at,manifest_hash=detail.manifest_hash))
            return {'items':result}
    @api.get(base+'/builds/{identity}',tags=['virtual-ecu'])
    def build_get(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            execution=session.get(ExperimentExecutionRow,identity)
            if execution is None or execution.project_id!=project_id:raise HTTPException(404,'vECU build not found in project')
            experiment,_,prepared=experiment_jobs.experiment_get(session,execution.experiment_id)
            if (experiment.kind!='virtual_ecu' or prepared.to_payload()['spec']['adapter_id']!=ADAPTER
                    or set(prepared.to_payload()['spec']['input'])!={'build_source_id'}):
                raise HTTPException(404,'vECU build not found in project')
            job=session.scalar(select(JobRow).where(JobRow.id==identity))
            if job is None:raise HTTPException(404,'vECU build not found in project')
            return experiment_jobs.detail(session,job)
    def save(kind,body,key):
        experiment_jobs._key(body.project_id,key,kind)
        request_hash=digest(body.model_dump(mode='json'));write_id=digest([kind,key])
        with jobs._write(db) as session:
            old=session.get(Row,(body.project_id,'vecu-write',write_id,1))
            if old:
                receipt=json.loads(old.manifest)
                if digest(receipt)!=old.manifest_hash or receipt['request_hash']!=request_hash:raise HTTPException(409,'Idempotency key was used for another request')
                return resolve(session,body.project_id,kind,receipt['ref'])
            identity=body.id or uid('vecu')
            latest=session.scalar(select(func.max(Row.revision)).where(Row.project_id==body.project_id,Row.kind==kind,Row.id==identity)) or 0
            if latest!=body.expected_revision:raise HTTPException(409,'Revision changed; reopen the latest record')
            value=body.model_dump(mode='json',exclude={'id','expected_revision'})|dict(id=identity,revision=latest+1,schema_version=kind+'/1',created_at=utcnow())
            if kind=='vecu-package':
                if body.runtime_id is not None:value['runtime']=current_runtime(body.runtime_id)
                else:
                    try:value['runtime']=handler().built_runtime(body.build_execution_id,body.project_id,session=session)
                    except (ValueError,OSError) as e:raise HTTPException(422,str(e)) from e
            elif kind=='vecu-test':
                package=resolve(session,body.project_id,'vecu-package',body.package_ref)
                packages=[package]
                if body.variant_package_ref is not None:packages.append(resolve(session,body.project_id,'vecu-package',body.variant_package_ref))
                if body.mode=='fixed_replay':
                    source=resolve(session,body.project_id,'vecu-input',body.input_ref)
                    if body.seed!=source['seed']:raise HTTPException(422,'Test seed must equal the saved input seed')
                    if any(f.end_step>=len(source['frames']) for f in body.faults):raise HTTPException(422,'Fault must end before the final replay step to observe recovery')
                else:
                    from .hil_beta import _test
                    saved=_test(session,body.project_id,body.hil_test_ref)
                    if any(p.get('runtime_id')!='reference-traffic-dut' and p.get('runtime',{}).get('kind')!='compiled_reference' for p in packages):
                        raise HTTPException(422,'CARLA supports its reference DUT or a completed registered C/C++ build')
                    if body.seed!=saved['experiment']['seed']:raise HTTPException(422,'Seed differs from saved HIL scenario')
                    value['hil_experiment']=saved['experiment']
            raw=canonical_bytes(value);sha=digest(value)
            session.add(Row(project_id=body.project_id,kind=kind,id=identity,revision=latest+1,manifest=raw,manifest_hash=sha))
            result=value|{'sha256':sha};receipt=dict(request_hash=request_hash,ref=ref(result))
            session.add(Row(project_id=body.project_id,kind='vecu-write',id=write_id,revision=1,manifest=canonical_bytes(receipt),manifest_hash=digest(receipt)))
            return result
    @api.post(base+'/packages',status_code=201,tags=['virtual-ecu'])
    def package_save(body:PackageSave,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer');return save('vecu-package',body,idempotency_key)
    @api.post(base+'/inputs',status_code=201,tags=['virtual-ecu'])
    def input_save(body:InputSave,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer');return save('vecu-input',body,idempotency_key)
    @api.post(base+'/tests',status_code=201,tags=['virtual-ecu'])
    def test_save(body:TestSave,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer');return save('vecu-test',body,idempotency_key)
    @api.get(base+'/records/{collection}',tags=['virtual-ecu'])
    def records(collection:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        kind={'packages':'vecu-package','inputs':'vecu-input','tests':'vecu-test'}.get(collection)
        if kind is None:raise HTTPException(404,'Unknown vECU collection')
        with db.read_session() as session:
            rows=session.scalars(select(Row).where(Row.project_id==project_id,Row.kind==kind).order_by(Row.id,Row.revision.desc()))
            return {'items':[read(row) for row in rows]}
    @api.get(base+'/records/{collection}/{identity}',tags=['virtual-ecu'])
    def record(collection:str,identity:str,request:Request,revision:int=Query(ge=1),project_id:str='default'):
        project_authorize(request,db,project_id)
        kind={'packages':'vecu-package','inputs':'vecu-input','tests':'vecu-test'}.get(collection)
        if kind is None:raise HTTPException(404,'Unknown vECU collection')
        with db.read_session() as session:return read(session.get(Row,(project_id,kind,identity,revision)))
    @api.post(base+'/tests/{identity}/preview',tags=['virtual-ecu'])
    def preview(identity:str,body:Action,request:Request):
        project_authorize(request,db,body.project_id,'Engineer')
        reference=dict(id=identity,revision=body.revision,sha256=body.sha256)
        with db.read_session() as session:test=resolve(session,body.project_id,'vecu-test',reference)
        spec=ExperimentCreate(project_id=body.project_id,name=test['name'],kind='virtual_ecu',adapter_id=ADAPTER,
            input={'test_ref':reference},seed=test['seed'])
        prepared=experiment_jobs.prepare(registry,spec)
        return dict(experiment=prepared.to_payload()['spec'],preview_hash=prepared.input_hash,prepared=prepared.to_payload(),worker_readiness='not_verified')
    @api.get(base+'/executions',tags=['virtual-ecu'])
    def executions(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            rows=session.execute(select(ExperimentRow,ExperimentExecutionRow,JobRow).join(ExperimentExecutionRow,ExperimentExecutionRow.experiment_id==ExperimentRow.id).join(JobRow,JobRow.id==ExperimentExecutionRow.job_id).where(ExperimentRow.project_id==project_id,ExperimentRow.kind=='virtual_ecu',JobRow.adapter_id==ADAPTER).order_by(JobRow.created_at.desc()).limit(100)).all()
            result=[]
            for experiment,execution,job in rows:
                prepared=experiment_jobs.experiment_get(session,experiment.id)[1]['prepared']
                if 'build_source_id' in prepared['spec']['input']:continue
                snap=prepared['input']
                result.append(dict(id=job.id,status=job.status,name=experiment.name,test_ref=ref(snap['test']),mode=snap['test']['mode']))
            return {'items':result}
