"""Immutable Apollo profiles and input tests, backed by the shared Experiment runtime."""
import json
from fastapi import Header, HTTPException, Query, Request
from sqlalchemy import func, select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentRow, ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .job_models import JobRow
from .apollo_lab_schemas import (ADAPTER, FIDELITY, ApolloConfig, ApolloProfileSave,
                              ApolloTestSave, RevisionRef, RawMetadata, validate_snapshot)
from .fpga_lab import resolve_design
from .fpga_lab_schemas import DesignAction, TimingConfig
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

SOURCES = [dict(id='apollo-quickstart',status='documented_structure',title='Apollo Camera Architecture Quick Start Guide',document='80-PN984-66 Rev AC',pages='5-14',sha256='c07d31fae2010693c29eef58edbf94296020411f97a765f73f9ef44d8451aca4'),
 dict(id='sm8750-overview',status='documented_structure',title='SM8750 Camera Overview',document='80-64995-82 Rev AA',pages='7-10',sha256='3529efe5c50976225aac092928855bb7dd8dbf5cc6a1e3c3d13c03bd00b7b43c')]
ASSUMPTIONS = ['Three TFE resources and shared OFE/IPE; channel mapping, FIFO scheduling, every rate and memory capacity are explicit assumptions.',
 'CPU NumPy/OpenCV reference pixels, not Qualcomm proprietary ISP output. Virtual time is independent of CPU wall time.',
 'Frame-level processing and uncompressed Bayer16/YUV444 byte accounting; no TP10/UBWC, automatic 3A, HDR, EVA/GME/EIS, firmware or electrical model.',
 'ZSL retains completed TFE Bayer; snapshot pins the latest eligible source frame and shares non-preemptive OFE/IPE with Preview.']


def read_revision(row, kind):
    if row is None:
        raise HTTPException(404, 'Apollo revision not found in project')
    value = json.loads(row.manifest)
    schema = 'apollo-profile/1' if kind == 'apollo-profile' else 'apollo-test/1'
    if (digest(value) != row.manifest_hash or value['id'] != row.id or value['project_id'] != row.project_id
            or value['revision'] != row.revision or value['schema_version'] != schema):
        raise HTTPException(409, 'Apollo revision integrity check failed')
    if kind == 'apollo-profile':
        ApolloConfig.model_validate(value['config'])
    else:
        ApolloTestSave.model_validate({k: value[k] for k in ('project_id', 'name', 'profile_ref', 'channels', 'captures', 'seed')})
    return value | {'sha256': row.manifest_hash}


def resolve_revision(session, project, ref, kind):
    ref = RevisionRef.model_validate(ref).model_dump()
    value = read_revision(session.get(Row, (project, kind, ref['id'], ref['revision'])), kind)
    if value['sha256'] != ref['sha256']:
        raise HTTPException(409, 'Apollo revision hash differs')
    return value


def resolve_test(session, project, ref):
    return resolve_revision(session, project, ref, 'apollo-test')


def hydrate_test(session, test):
    profile = resolve_revision(session, test['project_id'], test['profile_ref'], 'apollo-profile')
    channels = []
    for ch in test['channels']:
        value = ch | {'design':resolve_design(session,test['project_id'],ch['design_ref'])}
        if ch['raw']['asset_ref']:
            ref = ch['raw']['asset_ref']
            value['asset'] = read_asset(session.get(Row,(test['project_id'],'apollo-raw',ref['id'],1)))
            if ref['revision'] != 1 or value['asset']['sha256'] != ref['sha256']:raise HTTPException(409,'RAW asset reference mismatch')
        channels.append(value)
    validate_snapshot(profile['config'],channels,test['captures'])
    return dict(schema_version='apollo-input/1',test=test,profile=profile,channels=channels,execution_fidelity=FIDELITY)

def templates_payload():
    def preset(identity,name,changes=None,captures=None):
        return dict(id=identity,name=name,seed=42,profile=dict(config=ApolloConfig(**(changes or {})).model_dump()),
          channels=[dict(channel=i,vc=i,tfe=i%3,source_offset_us=120000,raw=dict(asset_ref=None,metadata=RawMetadata().model_dump(),repeat=False),config=TimingConfig(frames=30).model_dump()) for i in range(4)],captures=captures or [])
    return dict(schema_version='apollo-templates/1',fidelity=FIDELITY,profile_schema=ApolloConfig.model_json_schema(),sources=SOURCES,assumptions=ASSUMPTIONS,
      items=[preset('normal','Apollo · 4채널 Preview'),preset('csi','Apollo · CSI 병목',{'csi_lane_Mbps':200}),
        preset('ofe','Apollo · OFE 병목',{'ofe_us':20000}),preset('memory','Apollo · 메모리 병목',{'memory_MBps':500}),
        preset('zsl','Apollo · Preview + ZSL',captures=[dict(channel=0,at_us=t) for t in (400000,410000,420000)])])

def read_asset(row):
    if row is None:raise HTTPException(404,'RAW asset not found in project')
    value=json.loads(row.manifest)
    if digest(value)!=row.manifest_hash:raise HTTPException(409,'RAW asset integrity mismatch')
    return value | {'sha256':row.manifest_hash}

def asset_path(db,asset):
    from pathlib import Path
    h=asset['content_sha256']
    if len(h)!=64 or any(c not in '0123456789abcdef' for c in h):raise ValueError('Invalid RAW hash')
    return db.artifact_dir.parent / 'apollo-raw' / (h+'.raw')


def register_apollo_lab_routes(api, db, registry):
    prefix = '/hil-sim/apollo-lab'

    register_raw_routes(api,db,prefix)

    @api.get(prefix + '/templates', tags=['apollo-lab'])
    def templates(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return templates_payload() | dict(adapter_installed=registry.get(ADAPTER) is not None, worker_readiness='not_verified')

    def save_revision(body, request, key, kind):
        project_authorize(request, db, body.project_id, 'Engineer')
        experiment_jobs._key(body.project_id, key, kind+'-save')
        receipt_kind = kind+'-write'
        key_id, body_hash = digest(key), digest(body.model_dump(mode='json'))
        with jobs._write(db) as session:
            existing = session.get(Row, (body.project_id, receipt_kind, key_id, 1))
            if existing:
                receipt = json.loads(existing.manifest)
                if digest(receipt) != existing.manifest_hash or receipt['request_hash'] != body_hash:
                    raise HTTPException(409, 'Idempotency key identifies another Apollo write')
                return resolve_revision(session, body.project_id, receipt['ref'], kind)
            identity = body.id or uid('aplp' if kind == 'apollo-profile' else 'aplt')
            latest = session.scalar(select(func.max(Row.revision)).where(
                Row.project_id == body.project_id, Row.kind == kind, Row.id == identity)) or 0
            if body.expected_revision != latest or (body.id is None and body.expected_revision):
                raise HTTPException(409, 'Apollo document changed; reopen its latest revision')
            content = body.model_dump(exclude={'id', 'expected_revision'})
            value = content | dict(schema_version='apollo-profile/1' if kind == 'apollo-profile' else 'apollo-test/1',
                id=identity, revision=latest+1, created_at=utcnow(), fidelity=FIDELITY)
            if kind == 'apollo-profile':
                value.update(sources=SOURCES, assumptions=ASSUMPTIONS, target='SM8750 Apollo architecture / CPU reference')
            else:
                try:
                    hydrate_test(session, value)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            sha = digest(value)
            session.add(Row(project_id=body.project_id, kind=kind, id=identity, revision=latest+1,
                            manifest=canonical_bytes(value), manifest_hash=sha))
            receipt = dict(request_hash=body_hash, ref=dict(id=identity, revision=latest+1, sha256=sha))
            session.add(Row(project_id=body.project_id, kind=receipt_kind, id=key_id, revision=1,
                            manifest=canonical_bytes(receipt), manifest_hash=digest(receipt)))
            return value | {'sha256': sha}

    @api.post(prefix + '/profiles', status_code=201, tags=['apollo-lab'])
    def save_profile(body: ApolloProfileSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        return save_revision(body, request, idempotency_key, 'apollo-profile')

    @api.post(prefix + '/tests', status_code=201, tags=['apollo-lab'])
    def save_test(body: ApolloTestSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        return save_revision(body, request, idempotency_key, 'apollo-test')

    def list_revisions(request, project_id, kind):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == kind).order_by(Row.id, Row.revision.desc()))
            return {'items': [read_revision(row, kind) for row in rows]}

    @api.get(prefix + '/profiles', tags=['apollo-lab'])
    def profiles(request: Request, project_id: str = 'default'):
        return list_revisions(request, project_id, 'apollo-profile')

    @api.get(prefix + '/tests', tags=['apollo-lab'])
    def tests(request: Request, project_id: str = 'default'):
        return list_revisions(request, project_id, 'apollo-test')

    def get_revision(identity, revision, project_id, request, kind):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return read_revision(session.get(Row, (project_id, kind, identity, revision)), kind)

    @api.get(prefix + '/profiles/{identity}', tags=['apollo-lab'])
    def profile(identity: str, request: Request, revision: int = Query(ge=1), project_id: str = 'default'):
        return get_revision(identity, revision, project_id, request, 'apollo-profile')

    @api.get(prefix + '/tests/{identity}', tags=['apollo-lab'])
    def test(identity: str, request: Request, revision: int = Query(ge=1), project_id: str = 'default'):
        return get_revision(identity, revision, project_id, request, 'apollo-test')

    @api.post(prefix + '/tests/{identity}/preview', tags=['apollo-lab'])
    def preview(identity: str, body: DesignAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        reference = dict(id=identity, revision=body.revision, sha256=body.sha256)
        with db.read_session() as session:
            saved = resolve_test(session, body.project_id, reference)
        spec = ExperimentCreate(project_id=body.project_id, name=saved['name'], kind='apollo_camera',
            adapter_id=ADAPTER, input={'test_ref': reference}, seed=saved['seed'])
        prepared = experiment_jobs.prepare(registry, spec)
        return dict(experiment=prepared.to_payload()['spec'], preview_hash=prepared.input_hash,
                    fidelity=FIDELITY, worker_readiness='not_verified')

    @api.get(prefix + '/executions', tags=['apollo-lab'])
    def executions(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.execute(select(ExperimentRow, ExperimentExecutionRow, JobRow).join(
                ExperimentExecutionRow, ExperimentExecutionRow.experiment_id == ExperimentRow.id).join(JobRow, JobRow.id == ExperimentExecutionRow.job_id).where(
                    ExperimentRow.project_id == project_id, ExperimentRow.kind == 'apollo_camera')
                    .order_by(JobRow.created_at.desc()).limit(100)).all()
            return {'items': [dict(id=execution.job_id, status=job.status, name=experiment.name,
                test_ref=experiment_jobs.experiment_get(session, experiment.id)[1]['prepared']['spec']['input']['test_ref'])
                for experiment, execution, job in rows]}


def register_raw_routes(api,db,prefix):
    import hashlib, os, shutil, tempfile
    from fastapi.responses import FileResponse
    from pydantic import ValidationError
    limit=128*1024*1024
    directory=db.artifact_dir.parent/'apollo-raw';directory.mkdir(exist_ok=True)
    @api.post(prefix+'/assets',status_code=201,tags=['apollo-lab'])
    async def upload(request:Request,metadata:str=Query(),project_id:str='default',name:str=Query(default='RAW input',max_length=180)):
        project_authorize(request,db,project_id,'Engineer')
        try:m=RawMetadata.model_validate_json(metadata)
        except (ValidationError,ValueError) as exc:raise HTTPException(422,str(exc)) from exc
        if request.headers.get('content-type','').split(';')[0]!='application/octet-stream':raise HTTPException(415,'Upload original RAW bytes')
        if shutil.disk_usage(directory).free<3*limit:raise HTTPException(507,'Insufficient RAW storage; nothing deleted')
        fd,path=tempfile.mkstemp(dir=directory,suffix='.part');sha=hashlib.sha256();size=0
        try:
            with os.fdopen(fd,'wb') as stream:
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>limit:raise HTTPException(413,'RAW exceeds 128 MiB')
                    sha.update(chunk);stream.write(chunk)
            if not size or size%m.frame_bytes:raise HTTPException(422,'File length does not match RAW stride/frame dimensions')
            if size//m.frame_bytes>30:raise HTTPException(422,'At most 30 file frames')
            h=sha.hexdigest();target=directory/(h+'.raw')
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest()!=h:raise HTTPException(409,'Existing RAW bytes corrupted')
            else:os.replace(path,target)
            value=dict(schema_version='apollo-raw/1',id='raw_'+digest([h,m.model_dump()])[:32],revision=1,project_id=project_id,name=name,
                content_sha256=h,size_bytes=size,frames=size//m.frame_bytes,metadata=m.model_dump(),source_kind='user_raw')
            with jobs._write(db) as session:
                row=session.get(Row,(project_id,'apollo-raw',value['id'],1))
                if row:return read_asset(row)
                session.add(Row(project_id=project_id,kind='apollo-raw',id=value['id'],revision=1,manifest=canonical_bytes(value),manifest_hash=digest(value)))
            return value | {'sha256':digest(value)}
        finally:
            if os.path.exists(path):os.unlink(path)
    @api.get(prefix+'/assets',tags=['apollo-lab'])
    def assets(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            return {'items':[read_asset(r) for r in session.scalars(select(Row).where(Row.project_id==project_id,Row.kind=='apollo-raw'))]}
    @api.get(prefix+'/assets/{identity}',tags=['apollo-lab'])
    def asset(identity:str,request:Request,project_id:str='default'):
        project_authorize(request,db,project_id)
        with db.read_session() as session:return read_asset(session.get(Row,(project_id,'apollo-raw',identity,1)))
    @api.get(prefix+'/assets/{identity}/bytes',tags=['apollo-lab'])
    def raw_bytes(identity:str,request:Request,project_id:str='default'):
        value=asset(identity,request,project_id);path=asset_path(db,value)
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=value['content_sha256']:raise HTTPException(409,'RAW bytes corrupted')
        return FileResponse(path,media_type='application/octet-stream',filename='input.raw')
