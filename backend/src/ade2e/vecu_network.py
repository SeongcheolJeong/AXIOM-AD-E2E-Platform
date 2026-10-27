"""Native network authoring; execution remains on the immutable Experiment queue."""
import json
from fastapi import Header, HTTPException, Query, Request, Response
from sqlalchemy import select, func
from . import experiment_jobs, jobs
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .experiment_models import ExperimentRow, ExperimentExecutionRow, ExperimentArtifactRow
from .job_models import JobRow
from .experiment_schemas import ExperimentCreate
from .team_access import project_authorize
from .vecu_network_schemas import NetworkSave, NetworkTestSave, Action, PROFILE_ID, FAULT_KINDS

ADAPTER='vecu-network'
MODELS={'vecu-network-config':NetworkSave,'vecu-network-test':NetworkTestSave}
COLLECTIONS={'networks':'vecu-network-config','tests':'vecu-network-test'}

def ref(value):return {key:value[key] for key in ('id','revision','sha256')}

def read(row):
    if row is None:raise HTTPException(404,'Network revision not found in project')
    try:
        value=json.loads(row.manifest)
        if digest(value)!=row.manifest_hash or (value['project_id'],value['id'],value['revision'],value['schema_version'])!=(row.project_id,row.id,row.revision,row.kind+'/1'):
            raise ValueError('Identity mismatch')
        model=MODELS[row.kind]
        model.model_validate({key:value[key] for key in model.model_fields if key not in ('expected_revision','id') and key in value})
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(409,'Network saved revision integrity failed') from exc
    return value|{'sha256':row.manifest_hash}

def resolve(session,project,kind,reference):
    if hasattr(reference,'model_dump'):reference=reference.model_dump()
    value=read(session.get(Row,(project,kind,reference['id'],reference['revision'])))
    if value['sha256']!=reference['sha256']:raise HTTPException(409,'Network revision hash differs')
    return value

def register_vecu_network_routes(api,db,registry):
    base='/virtual-ecu-network'
    def handler():
        result=registry.get(ADAPTER)
        if result is None:raise HTTPException(422,'Optional native vECU network adapter is not installed')
        return result
    def status(profile_id):
        if profile_id!=PROFILE_ID:raise HTTPException(422,'Unknown registered network profile')
        try:return handler().readiness(profile_id)
        except (HTTPException,ValueError,OSError,AttributeError) as exc:
            return dict(available=False,status='not_configured',profile_id=profile_id,reason=str(getattr(exc,'detail',exc)))
    @api.get(base+'/catalog',tags=['virtual-ecu-network'])
    def catalog(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        try:registered=handler().catalog();error=None
        except (HTTPException,ValueError,OSError,AttributeError) as exc:
            registered={};error=str(getattr(exc,'detail',exc))
        if not isinstance(registered,dict):registered={'profiles':registered}
        return registered|dict(schema_version='vecu-network-catalog/1',error=error,
            network_example=NetworkSave(name='Four ECU native network').model_dump(mode='json'),fault_kinds=FAULT_KINDS,
            authoring='available',runtime_readiness='query_readiness',physical_hil='not_connected',
            classification='Host CAN/Ethernet functional network with public BSW and OS; not production AUTOSAR or physical bus validation',
            license_scope='internal_verification; code and distribution terms remain separate')
    @api.get(base+'/readiness',tags=['virtual-ecu-network'])
    def readiness(request:Request,project_id:str='default',profile_id:str=PROFILE_ID):
        project_authorize(request,db,project_id);return status(profile_id)
    def save(kind,body,key):
        experiment_jobs._key(body.project_id,key,kind)
        request_hash=digest(body.model_dump(mode='json'));write_id=digest([kind,key])
        with jobs._write(db) as session:
            old=session.get(Row,(body.project_id,'vecu-network-write',write_id,1))
            if old:
                receipt=json.loads(old.manifest)
                if digest(receipt)!=old.manifest_hash or receipt['request_hash']!=request_hash:
                    raise HTTPException(409,'Idempotency key was used for another request')
                return resolve(session,body.project_id,kind,receipt['ref'])
            identity=body.id or uid('vecunet')
            latest=session.scalar(select(func.max(Row.revision)).where(Row.project_id==body.project_id,Row.kind==kind,Row.id==identity)) or 0
            if latest!=body.expected_revision:raise HTTPException(409,'Revision changed; reopen the latest record')
            value=body.model_dump(mode='json',exclude={'id','expected_revision'})|dict(id=identity,revision=latest+1,schema_version=kind+'/1',created_at=utcnow())
            if kind=='vecu-network-test':
                resolve(session,body.project_id,'vecu-network-config',body.network_ref)
                if body.mode=='fixed_replay':
                    from .vecu import resolve as vecu_resolve
                    source=vecu_resolve(session,body.project_id,'vecu-input',body.input_ref)
                    if source['seed']!=body.seed:raise HTTPException(422,'Test seed must equal the saved input seed')
                    duration_ms=len(source['frames'])*source['period_ns']//1000000
                else:
                    from .hil_beta import _test
                    saved=_test(session,body.project_id,body.hil_test_ref)
                    if body.seed!=saved['experiment']['seed']:raise HTTPException(422,'Seed differs from saved HIL scenario')
                    value['hil_experiment']=saved['experiment'];duration_ms=18000
                if any(fault.end_ms>=duration_ms for fault in body.faults):
                    raise HTTPException(422,'Fault must end before the input window ends to observe recovery')
            sha=digest(value)
            session.add(Row(project_id=body.project_id,kind=kind,id=identity,revision=latest+1,manifest=canonical_bytes(value),manifest_hash=sha))
            result=value|{'sha256':sha};receipt=dict(request_hash=request_hash,ref=ref(result))
            session.add(Row(project_id=body.project_id,kind='vecu-network-write',id=write_id,revision=1,manifest=canonical_bytes(receipt),manifest_hash=digest(receipt)))
            return result
    @api.post(base+'/networks',status_code=201,tags=['virtual-ecu-network'])
    def save_network(body:NetworkSave,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer');return save('vecu-network-config',body,idempotency_key)
    @api.post(base+'/tests',status_code=201,tags=['virtual-ecu-network'])
    def save_test(body:NetworkTestSave,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer');return save('vecu-network-test',body,idempotency_key)
    def collection_kind(collection):
        if collection not in COLLECTIONS:raise HTTPException(404,'Unknown network collection')
        return COLLECTIONS[collection]
    @api.get(base+'/records/{collection}',tags=['virtual-ecu-network'])
    def records(collection:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id);kind=collection_kind(collection)
        with db.read_session() as session:
            rows=session.scalars(select(Row).where(Row.project_id==project_id,Row.kind==kind).order_by(Row.id,Row.revision.desc()))
            return {'items':[read(row) for row in rows]}
    @api.get(base+'/records/{collection}/{identity}',tags=['virtual-ecu-network'])
    def record(collection:str,identity:str,request:Request,revision:int=Query(ge=1),project_id:str='default'):
        project_authorize(request,db,project_id);kind=collection_kind(collection)
        with db.read_session() as session:return read(session.get(Row,(project_id,kind,identity,revision)))
    def spec_for(identity,body):
        reference=dict(id=identity,revision=body.revision,sha256=body.sha256)
        with db.read_session() as session:test=resolve(session,body.project_id,'vecu-network-test',reference)
        return ExperimentCreate(project_id=body.project_id,name=test['name'],kind='virtual_ecu',adapter_id=ADAPTER,
            input={'test_ref':reference},seed=test['seed'])
    @api.post(base+'/tests/{identity}/preview',tags=['virtual-ecu-network'])
    def preview(identity:str,body:Action,request:Request):
        project_authorize(request,db,body.project_id,'Engineer')
        prepared=experiment_jobs.prepare(registry,spec_for(identity,body))
        return dict(experiment=prepared.to_payload()['spec'],preview_hash=prepared.input_hash,prepared=prepared.to_payload(),worker_readiness='not_verified')
    @api.post(base+'/tests/{identity}/executions',status_code=202,tags=['virtual-ecu-network'])
    def execute(identity:str,body:Action,request:Request,idempotency_key:str=Header(alias='Idempotency-Key')):
        project_authorize(request,db,body.project_id,'Engineer')
        key='vecu-network:'+idempotency_key
        if len(key)>200:key='vecu-network:'+digest(idempotency_key)
        experiment_jobs._key(body.project_id,idempotency_key,'vecu-network-execution')
        spec=spec_for(identity,body)
        experiment=experiment_jobs.create(db,registry,spec,key)
        return experiment_jobs.submit(db,registry,experiment['id'],experiment['preview_hash'],key)
    def execution_row(session,identity,project_id):
        row=session.scalar(select(JobRow).join(ExperimentExecutionRow,ExperimentExecutionRow.job_id==JobRow.id).where(
            JobRow.id==identity,JobRow.adapter_id==ADAPTER,ExperimentExecutionRow.project_id==project_id))
        if row is None:raise HTTPException(404,'Network execution not found in project')
        return row
    @api.get(base+'/executions',tags=['virtual-ecu-network'])
    def executions(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            rows=session.execute(select(ExperimentRow,JobRow).join(ExperimentExecutionRow,ExperimentExecutionRow.experiment_id==ExperimentRow.id).join(
                JobRow,JobRow.id==ExperimentExecutionRow.job_id).where(ExperimentRow.project_id==project_id,
                JobRow.adapter_id==ADAPTER).order_by(JobRow.created_at.desc()).limit(100)).all()
            items=[]
            for experiment,job in rows:
                prepared=experiment_jobs.experiment_get(session,experiment.id)[1]['prepared']
                test=prepared['input'].get('test',{})
                items.append(dict(id=job.id,status=job.status,name=experiment.name,test_ref=prepared['spec']['input']['test_ref'],
                    mode=test.get('mode'),faults=test.get('faults',[]),created_at=job.created_at))
            return {'items':items}
    @api.get(base+'/executions/{identity}',tags=['virtual-ecu-network'])
    def execution(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:return experiment_jobs.detail(session,execution_row(session,identity,project_id))
    @api.get(base+'/executions/{identity}/causality',tags=['virtual-ecu-network'])
    def causality(identity:str,request:Request,project_id:str='default'):
        from .vecu_network_causality import derive_causality
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            row=execution_row(session,identity,project_id)
            detail=experiment_jobs.detail(session,row)
            if row.status!='completed':raise HTTPException(409,'Causality requires a completed retained execution')
            artifacts=list(session.scalars(select(ExperimentArtifactRow).where(ExperimentArtifactRow.job_id==identity,ExperimentArtifactRow.name=='vecu-trace.json')))
            if len(artifacts)!=1:raise HTTPException(409,'A unique original control trace is required')
            artifact=artifacts[0]
            if artifact.size_bytes>64*1024*1024:raise HTTPException(422,'Causality trace exceeds the bounded analysis profile')
            path=experiment_jobs.artifact_path(db,artifact)
            try:report=derive_causality(json.loads(path.read_bytes()),row.prepared['input'])
            except (ValueError,KeyError,TypeError,IndexError) as exc:raise HTTPException(409,'Causality evidence is incomplete or unsupported') from exc
            report.update(execution_id=identity,project_id=project_id,manifest_sha256=detail.manifest_hash,
                          test_ref=row.prepared['spec']['input']['test_ref'],seed=row.prepared['spec']['seed'],adapter_version=row.adapter_version,
                          fidelity=detail.fidelity,source_artifact=dict(id=artifact.id,name=artifact.name,sha256=artifact.sha256,size_bytes=artifact.size_bytes))
            return report|{'sha256':digest(report)}
    @api.post(base+'/executions/{identity}/cancel',tags=['virtual-ecu-network'])
    def cancel(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id,'Engineer')
        with db.read_session() as session:execution_row(session,identity,project_id)
        return jobs.cancel_job(db,identity,kind='experiment')
    @api.get(base+'/executions/{identity}/export',tags=['virtual-ecu-network'])
    def export(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            experiment_jobs.detail(session,execution_row(session,identity,project_id))
            subject=session.get(ExperimentExecutionRow,identity)
            if subject.manifest is None:raise HTTPException(409,'Execution is not terminal; no immutable receipt is available')
            return Response(subject.manifest,media_type='application/json',headers={
                'Content-Disposition':f'attachment; filename="{identity}.json"','X-Content-SHA256':subject.manifest_hash})
