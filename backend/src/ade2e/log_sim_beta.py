"""Internal beta: owned uploads, explicit input selection, versioned settings/reviews."""
import csv
import hashlib
import io
import json
import shutil
import tempfile
from pathlib import Path
from threading import RLock
from typing import Literal

from fastapi import Query, Header, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from . import log_sim, log_sim_replays as replay, mcap_ingest
from .base_schemas import StrictModel
from .project_recordings import authorize_recording_source, recording_source_scope
from .project_recording_models import ProjectRecordingGrantRow
from .recording_models import RecordingRow, RecordingMessageRow
from .recording_schemas import RecordingImportMetadata
from .recordings import import_recording, recording_detail, read_message_bytes
from .replay_spec_schemas import ReferenceController
from .scene_sequence_schemas import PlanningEgo, SceneSequenceCreate
from .scene_sequences import prepare_sequence
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Catalog
from .team_access import project_authorize

LOCK = RLock()
SETTINGS = 'log-comparison-settings'
REVIEW = 'log-case-review'


class InputSelection(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    recording_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=180)
    ego_channel_id: int = Field(ge=0, le=65535)
    objects_channel_id: int = Field(ge=0, le=65535)
    camera_channel_id: int | None = Field(default=None, ge=0, le=65535)
    first_sample: int = Field(default=0, ge=0, le=99999)
    sample_count: int = Field(default=40, ge=2, le=512)


class InputRegister(InputSelection):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class Knobs(StrictModel):
    cruise_speed_mps: float = Field(default=8., ge=0, le=50, allow_inf_nan=False)
    time_headway_s: float = Field(default=1.5, ge=.1, le=10, allow_inf_nan=False)
    stop_buffer_m: float = Field(default=2., ge=0, le=30, allow_inf_nan=False)


class SettingsCreate(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    id: str | None = Field(default=None, min_length=1, max_length=100)
    expected_revision: int = Field(default=0, ge=0)
    name: str = Field(min_length=1, max_length=180)
    baseline: Knobs = Field(default_factory=Knobs)
    candidate: Knobs = Field(default_factory=lambda: Knobs(time_headway_s=3.))


class SettingsRef(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ReviewCreate(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(default=0, ge=0)
    status: Literal['unreviewed', 'investigate', 'reviewed'] = 'unreviewed'
    tags: list[str] = Field(default_factory=list, max_length=12)
    note: str = Field(default='', max_length=4000)
    time_s: float = Field(default=0, ge=0, allow_inf_nan=False)


def settings_profiles(session, project, ref):
    row = session.get(Catalog, (project, SETTINGS, ref.id, ref.revision))
    if row is None:
        raise HTTPException(404, 'Comparison settings revision not found')
    manifest = json.loads(row.manifest)
    if digest(manifest) != row.manifest_hash:
        raise HTTPException(409, 'Comparison settings integrity check failed')
    value = manifest['definition']
    if digest({k: v for k, v in value.items() if k != 'sha256'}) != ref.sha256 or value['sha256'] != ref.sha256:
        raise HTTPException(409, 'Comparison settings hash changed')
    return value['profiles']


def _owned_recording(db, request, project, identity):
    authorize_recording_source(request, db, project, identity)
    with db.read_session() as session:
        return recording_detail(session, session.get(RecordingRow, identity)).model_dump(mode='json')


def inspect_input(db, body):
    with db.read_session() as session:
        row = session.get(RecordingRow, body.recording_id)
        if row is None: raise HTTPException(404, 'Recording not found')
        detail = recording_detail(session, row)
        schemas = {s.id: s.name for s in detail.schemas}
        roles = {c.id: schemas.get(c.schema_id) for c in detail.channels}
        for channel, expected in [(body.ego_channel_id, 'axiom.PlanningEgo.v1'),
                                  (body.objects_channel_id, 'axiom.ObjectTracks.v1')]:
            if roles.get(channel) != expected: raise HTTPException(422, 'Unsupported channel schema; expected '+expected)
        if body.camera_channel_id is not None and roles.get(body.camera_channel_id) != 'foxglove.CompressedImage':
            raise HTTPException(422, 'Camera must use foxglove.CompressedImage')
        rows = list(session.scalars(select(RecordingMessageRow).where(RecordingMessageRow.recording_id == row.id,
            RecordingMessageRow.channel_id == body.ego_channel_id).order_by(RecordingMessageRow.log_time_key, RecordingMessageRow.ordinal)))
        selected = rows[body.first_sample:body.first_sample+body.sample_count]
        if len(selected) != body.sample_count: raise HTTPException(422, 'Selected interval exceeds the original ego sample count')
        try:
            ego = PlanningEgo.model_validate_json(read_message_bytes(db, row, selected[0]))
            definition = SceneSequenceCreate(name=body.title, project_id=body.project_id, source_type='recording',
                recording_id=row.id, ego_channel_id=body.ego_channel_id, object_channel_id=body.objects_channel_id,
                ego_ordinals=[r.ordinal for r in selected], world_frame=ego.frame_id, clock_domain='original-log-clock',
                object_max_age_ns='0', note='User selected recorded input; original pixels are context only.')
            snapshot = prepare_sequence(session, db, definition)
            if any(f['object_drop'] for f in snapshot['frames']): raise ValueError('Exact object observations are missing; select another interval')
            if any(f['objects']['coordinate_frame'] != 'world' for f in snapshot['frames']):
                raise ValueError('Beta requires explicitly declared world-coordinate objects')
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, 'Input not ready: '+str(exc)) from exc
        origin, end = int(selected[0].log_time_key), int(selected[-1].log_time_key)
        ref = dict(id=row.id, sha256=row.sha256, ego_channel_id=body.ego_channel_id, objects_channel_id=body.objects_channel_id,
            camera_channel_id=body.camera_channel_id, ego_ordinals=[r.ordinal for r in selected],
            time_origin_ns=str(origin), start_time_ns=str(origin), end_time_ns=str(end))
        result = dict(schema_version='log-input-preview/1', request=body.model_dump(exclude={'preview_hash'}),
            recording_ref=ref, source_kind=row.payload['source_kind'], source_use_scope=recording_source_scope(row),
            frame_count=len(selected), original_frame_count=len(rows), duration_s=(end-origin)/1e9,
            sequence_sha256=digest(snapshot), camera_available=body.camera_channel_id is not None,
            world_frame=ego.frame_id, limitations=snapshot['limitations'], ready=True)
        return result | {'preview_hash': digest(result)}


def _grant_upload(db, project, recording_id, upload_hash):
    """Only called after receiving these bytes, never from a client-supplied legacy ID."""
    with LOCK, db.session.begin() as session:
        previous = session.scalar(select(ProjectRecordingGrantRow).where(ProjectRecordingGrantRow.project_id == project,
            ProjectRecordingGrantRow.recording_id == recording_id))
        if previous: return
        row = session.get(RecordingRow, recording_id)
        manifest = dict(schema_version='project-recording-grant/1', id=uid('source_grant'), project_id=project,
            recording_id=row.id, recording_sha256=row.sha256, created_at=utcnow(), note='Bytes uploaded into this project by an Engineer',
            source_kind=row.payload['source_kind'], source=row.payload['source'], source_metadata=row.payload.get('metadata', []),
            source_use_scope=recording_source_scope(row), scope='original_source_read_and_supported_sequence_authoring',
            limitations=['Upload is not a commercial rights or calibration approval.'])
        session.add(ProjectRecordingGrantRow(id=manifest['id'], project_id=project, recording_id=row.id,
            recording_sha256=row.sha256, created_at=manifest['created_at'], request_key='log-upload:'+digest([project,row.id]),
            request_hash=upload_hash, manifest=canonical_bytes(manifest), manifest_hash=digest(manifest)))


def _upload_info(session, row):
    detail = recording_detail(session, row).model_dump(mode='json')
    return {k: detail[k] for k in ('id','name','sha256','size_bytes','source_kind','channels','schemas','duration_s','message_count')}


def register_log_sim_beta_routes(api, db):
    @api.post('/log-sim/uploads', status_code=201, tags=['Log Sim beta'])
    async def upload(request: Request, project_id: str = 'default', name: str = Query(default='User MCAP',min_length=1,max_length=180)):
        project_authorize(request, db, project_id, 'Engineer')
        if request.headers.get('content-type','').split(';')[0] != 'application/octet-stream':
            raise HTTPException(415, 'Upload original MCAP bytes as application/octet-stream')
        metadata = RecordingImportMetadata(name=name, source_kind='unknown', source='User upload to project '+project_id)
        declared = request.headers.get('content-length')
        if declared and (not declared.isdigit() or int(declared)>mcap_ingest.MAX_UPLOAD_BYTES):
            raise HTTPException(413, 'Upload limit is 128 MiB')
        if shutil.disk_usage(db.recording_dir).free < 3*mcap_ingest.MAX_UPLOAD_BYTES:
            raise HTTPException(507, 'Insufficient recording storage. Ask the operator to add capacity; no data was removed.')
        with tempfile.TemporaryDirectory(dir=db.recording_staging_dir) as temp:
            path=Path(temp)/'original.mcap'; checksum=hashlib.sha256(); size=0
            with path.open('wb') as stream:
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>mcap_ingest.MAX_UPLOAD_BYTES: raise HTTPException(413, 'Upload limit is 128 MiB')
                    checksum.update(chunk);stream.write(chunk)
            try:
                value,_=await run_in_threadpool(import_recording, db, LOCK, path, metadata, checksum.hexdigest(), size)
                await run_in_threadpool(_grant_upload, db, project_id, value.id, checksum.hexdigest())
            except mcap_ingest.RecordingValidationError as exc: raise HTTPException(exc.status_code, str(exc)) from exc
            except OSError as exc: raise HTTPException(507, 'Recording storage unavailable; retry the same file after recovery') from exc
        with db.session.begin() as session:
            replay._put(session, project_id, 'log-upload', value.id, dict(id=value.id, created_at=value.created_at, sha256=value.sha256))
            return _upload_info(session, session.get(RecordingRow,value.id))

    @api.get('/log-sim/uploads', tags=['Log Sim beta'])
    def uploads(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            values=replay._values(session, project_id, 'log-upload')
            result=[]
            for value in values:
                authorize_recording_source(request, db, project_id, value['id'])
                result.append(_upload_info(session, session.get(RecordingRow,value['id'])))
            return {'items':result}

    @api.post('/log-sim/inputs/inspect', tags=['Log Sim beta'])
    def inspect(body: InputSelection, request: Request):
        project_authorize(request, db, body.project_id)
        _owned_recording(db, request, body.project_id, body.recording_id)
        return inspect_input(db, body)

    @api.post('/log-sim/inputs', status_code=201, tags=['Log Sim beta'])
    def register(body: InputRegister, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        _owned_recording(db, request, body.project_id, body.recording_id)
        p=inspect_input(db, body)
        if p['preview_hash']!=body.preview_hash: raise HTTPException(409, 'Input selection changed; inspect again')
        identity='recorded-'+digest([body.project_id,p['recording_ref']])[:32]
        definition=dict(kind='recorded',title=body.title, recording_ref=p['recording_ref'], frame_count=p['frame_count'],
            duration_s=p['duration_s'], annotation_source='recorded_objects_origin_unverified',
            input_preview_hash=p['preview_hash'], original_frame_count=p['original_frame_count'])
        with LOCK:
            with db.read_session() as session:
                prior=log_sim._catalog(session,body.project_id,log_sim.SOURCE_KIND,identity)
            if prior:
                if prior['definition']!=definition: raise HTTPException(409, 'This interval is already registered with another title; use the retained source')
                return prior
            with db.read_session() as session:
                ids=set(session.scalars(select(Catalog.id).where(Catalog.project_id==body.project_id,Catalog.kind==log_sim.SOURCE_KIND)))
                for other_id in ids:
                    other=log_sim._catalog(session,body.project_id,log_sim.SOURCE_KIND,other_id)['definition']
                    ref=other.get('recording_ref',{})
                    if other.get('kind')=='recorded' and ref.get('id')==body.recording_id and ref.get('ego_channel_id')!=body.ego_channel_id:
                        raise HTTPException(422,'Beta supports one selected ego stream per original recording')
                    if other.get('kind')=='recorded' and ref.get('id')==body.recording_id and ref.get('ego_channel_id')==body.ego_channel_id and set(ref.get('ego_ordinals',[])) & set(p['recording_ref']['ego_ordinals']):
                        raise HTTPException(409,'This source already contains overlapping samples; select a non-overlapping interval or reuse the registered source')
            return log_sim._store(db,body.project_id,log_sim.SOURCE_KIND,identity,definition)

    @api.get('/log-sim/comparison-settings', tags=['Log Sim beta'])
    def list_settings(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session: return {'items':replay._values(session,project_id,SETTINGS)}

    @api.post('/log-sim/comparison-settings', status_code=201, tags=['Log Sim beta'])
    def save_settings(body: SettingsCreate, request: Request, idempotency_key: str=Header(alias='Idempotency-Key',min_length=1,max_length=180)):
        project_authorize(request,db,body.project_id,'Engineer')
        identity=body.id or 'logsettings_'+digest([body.project_id,idempotency_key])[:32]
        operation='settings_'+digest([body.project_id,idempotency_key])[:32]
        with LOCK, db.session.begin() as session:
            old=log_sim._catalog(session,body.project_id,'log-beta-write',operation)
            if old:
                if old['definition']['request']!=body.model_dump(): raise HTTPException(409,'Idempotency key belongs to another settings write')
                return old['definition']['result']
            previous=log_sim._catalog(session,body.project_id,SETTINGS,identity)
            revision=previous['revision'] if previous else 0
            if body.expected_revision!=revision: raise HTTPException(409,'Settings revision changed; reload before saving')
            profiles={side:dict(id=identity+':'+side,label=('A · ' if side=='baseline' else 'B · ')+body.name,
                controller=ReferenceController(**getattr(body,side).model_dump()).model_dump()) for side in ('baseline','candidate')}
            value=dict(id=identity,revision=revision+1,name=body.name,profiles=profiles,created_at=utcnow(),project_id=body.project_id)
            value['sha256']=digest(value)
            replay._put(session,body.project_id,SETTINGS,identity,value)
            replay._put(session,body.project_id,'log-beta-write',operation,{'request':body.model_dump(),'result':value})
            return value

    @api.get('/log-sim/reviews', tags=['Log Sim beta'])
    def reviews(request: Request, project_id: str='default', source_id: str='', case_id: str=''):
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            values=replay._values(session,project_id,REVIEW)
            values=[v for v in values if (not case_id or v['case_id']==case_id) and (not source_id or v['source_id']==source_id)]
            for v in values:
                source=log_sim._source(session,project_id,v['source_id']); replay._access(request,db,project_id,{'source_kind':source['kind'],'recording_ref':source.get('recording_ref')})
            return {'items':values}

    @api.post('/log-sim/reviews',status_code=201,tags=['Log Sim beta'])
    def save_review(body: ReviewCreate, request: Request, idempotency_key: str=Header(alias='Idempotency-Key',min_length=1,max_length=180)):
        principal=project_authorize(request,db,body.project_id,'Engineer')
        if any(not tag.strip() or len(tag)>60 for tag in body.tags):raise HTTPException(422,'Tags require 1..60 characters')
        operation='review_'+digest([body.project_id,idempotency_key])[:32]
        with LOCK, db.session.begin() as session:
            source=log_sim._source(session,body.project_id,body.source_id)
            replay._access(request,db,body.project_id,{'source_kind':source['kind'],'recording_ref':source.get('recording_ref')})
            case=log_sim._catalog(session,body.project_id,log_sim.CASE_KIND,body.case_id)
            if not case or case['definition']['source_id']!=body.source_id or case['definition']['source_hash']!=source['source_hash']:
                raise HTTPException(404,'Case not linked to this source')
            if body.time_s>source['duration_s']:raise HTTPException(422,'Review time exceeds original source')
            old=log_sim._catalog(session,body.project_id,'log-beta-write',operation)
            if old:
                if old['definition']['request']!=body.model_dump():raise HTTPException(409,'Idempotency key belongs to another review')
                return old['definition']['result']
            previous=log_sim._catalog(session,body.project_id,REVIEW,body.case_id); revision=previous['revision'] if previous else 0
            if body.expected_revision!=revision:raise HTTPException(409,'Review changed; reload before saving')
            value=body.model_dump()|dict(id=body.case_id,revision=revision+1,created_at=utcnow(),source_hash=source['source_hash'],
                case_ref=case['definition'],author=principal.user_label,verdict='not_evaluated')
            replay._put(session,body.project_id,REVIEW,body.case_id,value)
            replay._put(session,body.project_id,'log-beta-write',operation,{'request':body.model_dump(),'result':value})
            return value

    @api.get('/log-sim/batches/{identity}/export',tags=['Log Sim beta'])
    def export(identity: str,request:Request,project_id:str='default',format:Literal['json','csv']='json'):
        from .log_sim_batches import BATCH, report_batch, _authorize_saved
        project_authorize(request,db,project_id)
        with db.read_session() as session:
            batch=replay._get(session,project_id,BATCH,identity);_authorize_saved(db,request,batch)
            report=report_batch(db,session,batch)
            if format=='json':return report
            out=io.StringIO();writer=csv.writer(out);writer.writerow(['candidate_id','source_id','start_s','end_s','samples','decision_difference_ratio','opposite_acceleration_ratio','max_acceleration_delta_mps2','verdict'])
            for c in report['candidates']:
                m=c['metrics'];writer.writerow([c['id'],c['source_id'],c['time_s'],c['end_time_s'],m['sample_count'],m['decision_difference_ratio'],m['opposite_acceleration_ratio'],m['max_abs_acceleration_delta_mps2'],'not_evaluated'])
            return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="log-comparison.csv"'})
