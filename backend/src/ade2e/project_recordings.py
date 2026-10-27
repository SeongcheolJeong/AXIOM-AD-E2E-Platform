"""Project reads of explicitly granted original sources; legacy APIs stay closed."""
import hashlib
import json
import os
from typing import Annotated
from urllib.parse import quote

from fastapi import Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from starlette.background import BackgroundTask

from .base_schemas import Name, StrictModel
from .project_recording_models import ProjectRecordingGrantRequestRow, ProjectRecordingGrantRow
from .recording_models import RecordingChannelRow, RecordingRow, RecordingSchemaRow
from .recording_schemas import RecordingSamples, RecordingSummary
from .recordings import _get_recording, _managed_path, recording_samples
from .scene_sequence_schemas import Hash
from .service import canonical_bytes, uid, utcnow
from .team_access import project_authorize


class RecordingGrantCreate(StrictModel):
    project_id: Name
    recording_id: Name
    recording_sha256: Hash
    note: str = Field(default='', max_length=4000)


def _grant_detail(row):
    try:
        value = json.loads(row.manifest)
        if (hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash or canonical_bytes(value) != row.manifest
                or value['id'] != row.id or value['project_id'] != row.project_id
                or value['recording_id'] != row.recording_id or value['recording_sha256'] != row.recording_sha256
                or value['created_at'] != row.created_at or value['schema_version'] != 'project-recording-grant/1'):
            raise ValueError('Grant identity differs from original manifest')
        return value | {'manifest_hash': row.manifest_hash}
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(409, 'Recording grant integrity check failed') from error


def _verified_recording_stream(db, row):
    """Verify and rewind the same descriptor subsequently used for downloads."""
    path = _managed_path(db.recording_dir, row.storage_key)
    expected_size = row.payload['size_bytes']
    stream = path.open('rb')
    try:
        if type(expected_size) is not int or expected_size < 0 or os.fstat(stream.fileno()).st_size != expected_size:
            raise HTTPException(409, 'Original recording byte size differs from its pinned source')
        total, checksum = 0, hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            total += len(chunk)
            if total > expected_size:
                raise HTTPException(409, 'Original recording grew during its source check')
            checksum.update(chunk)
        if total != expected_size or checksum.hexdigest() != row.sha256 or row.payload['sha256'] != row.sha256:
            raise HTTPException(409, 'Original recording bytes differ from the pinned source SHA-256')
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def verify_recording_bytes(db, row):
    """Hash the bounded original, distinct from the payload index/message checks."""
    _verified_recording_stream(db, row).close()


def _download_url(recording_id, project_id):
    return f'/api/v1/project-recordings/{quote(recording_id, safe="")}/download?project_id={quote(project_id, safe="")}'


def recording_source_scope(recording):
    """Interpret only the explicit versioned metadata; unknown stays unknown."""
    metadata = recording.payload.get('metadata', []) if hasattr(recording, 'payload') else recording
    entries = [item for item in metadata if isinstance(item, dict) and item.get('name') == 'axiom.source_provenance']
    if not entries:
        return {'status': 'unknown', 'use_scope': 'unknown', 'commercial_use': 'unknown',
                'limitations': ['The original recording declares no supported source-use provenance.']}
    if len(entries) != 1:
        raise HTTPException(409, 'Ambiguous duplicate source-use provenance metadata')
    declaration = entries[0].get('metadata', {})
    if (not isinstance(declaration, dict) or declaration.get('schema_version') != 'axiom.source-provenance/1'
            or declaration.get('use_scope') != 'isolated_noncommercial_research'
            or declaration.get('commercial_use') != 'false'
            or declaration.get('source_dataset') != 'nuScenes mini'
            or not isinstance(declaration.get('converter_version'), str) or not declaration['converter_version'].strip()
            or not isinstance(declaration.get('lineage_json'), str)
            or len(declaration['lineage_json'].encode()) > 128 * 1024):
        raise HTTPException(409, 'Known source-use provenance is malformed or unsupported; restrictions cannot be relaxed')
    try:
        from .scene_sequences import _json_exact
        lineage = _json_exact(declaration['lineage_json'].encode())
        if not isinstance(lineage, dict):raise ValueError('Source lineage must be an object')
    except (ValueError, UnicodeError, TypeError, RecursionError) as error:
        raise HTTPException(409, 'Declared source lineage is invalid') from error
    full_entries = [item for item in metadata if isinstance(item,dict) and item.get('name') == 'axiom.original_lineage']
    reference = lineage.get('original_lineage_metadata')
    full_verified = False
    if full_entries or reference is not None:
        if (len(full_entries) != 1 or not isinstance(reference,dict)
                or set(reference) != {'name','field','sha256'} or reference['name'] != 'axiom.original_lineage'
                or reference['field'] != 'lineage_json'):
            raise HTTPException(409, 'Original lineage metadata reference is ambiguous or incomplete')
        full_metadata = full_entries[0].get('metadata', {})
        original_json = full_metadata.get('lineage_json') if isinstance(full_metadata,dict) else None
        if (not isinstance(full_metadata,dict) or full_metadata.get('schema_version') != 'axiom.original-lineage/1' or not isinstance(original_json,str)
                or len(original_json.encode()) > 1536 * 1024):
            raise HTTPException(409, 'Original lineage metadata exceeds its supported contract')
        try:
            def pairs(values):
                result = {}
                for key,value in values:
                    if key in result:raise ValueError('Duplicate original lineage key')
                    result[key] = value
                return result
            def constant(value):raise ValueError('Nonfinite original lineage value')
            original = json.loads(original_json,object_pairs_hook=pairs,parse_constant=constant)
            original_bytes = original_json.encode()
            if (not isinstance(original,dict) or canonical_bytes(original) != original_bytes
                    or hashlib.sha256(original_bytes).hexdigest() != reference['sha256']):
                raise ValueError('Original lineage SHA-256 differs from the summary reference')
        except (ValueError,TypeError,UnicodeError,RecursionError) as error:
            raise HTTPException(409, 'Original lineage metadata integrity check failed') from error
        full_verified = True
    return {'status': 'source_declared', 'schema_version': declaration['schema_version'],
            'use_scope': declaration['use_scope'], 'commercial_use': False,
            'source_dataset': declaration['source_dataset'], 'converter_version': declaration['converter_version'],
            'lineage': lineage, 'full_lineage_metadata_verified': full_verified,
            'limitations': ['A recording grant preserves these restrictions; it cannot grant commercial use.']}


def recording_use_scope(payload: dict) -> dict:
    """Source-use contract for scene sequence snapshots (unrestricted is never inferred)."""
    return recording_source_scope(payload.get('metadata', []))


def authorize_recording_source(request, db, project_id, recording_id):
    """Return grant scope; project tokens require a grant even in default project.

    Only the operator's default project keeps legacy source compatibility. This
    helper checks the pinned catalog identity; freeze separately hashes originals.
    """
    principal = project_authorize(request, db, project_id, 'Viewer')
    with db.read_session() as session:
        row = session.scalar(select(ProjectRecordingGrantRow).where(
            ProjectRecordingGrantRow.project_id == project_id, ProjectRecordingGrantRow.recording_id == recording_id))
        if row is None and not (principal.mode != 'project' and project_id == 'default'):
            raise HTTPException(404, 'Granted project recording source not found')
        recording = _get_recording(session, recording_id)
        if row is not None:
            grant = _grant_detail(row)
            if recording.sha256 != row.recording_sha256 or recording.payload['sha256'] != row.recording_sha256:
                raise HTTPException(409, 'Granted recording SHA-256 differs from the current pinned source')
            if (recording.payload.get('metadata', []) != grant['source_metadata']
                    or recording_source_scope(recording) != grant['source_use_scope']):
                raise HTTPException(409, 'Granted recording source-use metadata differs from its immutable grant')
            return {'access_scope': 'explicit_project_grant', 'grant': grant,
                    'recording_id': recording_id, 'recording_sha256': row.recording_sha256,
                    'source_use_scope': recording_source_scope(recording)}
        return {'access_scope': 'operator_default_legacy', 'grant': None,
                'recording_id': recording_id, 'recording_sha256': recording.sha256,
                'source_use_scope': recording_source_scope(recording)}


def register_project_recording_routes(api, db):
    @api.post('/project-recordings/grants', status_code=201, tags=['project recordings'],
              openapi_extra={'x-axiom-min-role': 'Admin', 'x-axiom-operator-only': True})
    def create_grant(body: RecordingGrantCreate, request: Request,
                     idempotency_key: Annotated[str, Header(min_length=1, max_length=180, alias='Idempotency-Key')]):
        if request.state.principal.mode == 'project':
            raise HTTPException(403, 'Only the operator can grant a legacy recording source')
        project_authorize(request, db, body.project_id, 'Admin')
        request_hash = hashlib.sha256(canonical_bytes(body.model_dump(mode='json'))).hexdigest()
        request_key = 'project-recording:' + idempotency_key
        with db.session.begin() as session:
            attempt = session.get(ProjectRecordingGrantRequestRow,request_key)
            if attempt is not None:
                if attempt.request_hash != request_hash:
                    raise HTTPException(409, 'Idempotency key is already bound to another recording grant')
                previous = session.get(ProjectRecordingGrantRow,attempt.grant_id)
                if previous is None:raise HTTPException(409,'Recording grant request lost its immutable grant')
                _grant_detail(previous)
                recording = _get_recording(session, previous.recording_id)
                verify_recording_bytes(db, recording)
                if recording.sha256 != previous.recording_sha256:
                    raise HTTPException(409, 'Granted recording identity changed')
                return _grant_detail(previous)
            recording = _get_recording(session, body.recording_id)
            if recording.sha256 != body.recording_sha256:
                raise HTTPException(409, 'Recording grant must pin the exact imported source SHA-256')
            verify_recording_bytes(db, recording)
            existing = session.scalar(select(ProjectRecordingGrantRow).where(
                ProjectRecordingGrantRow.project_id == body.project_id, ProjectRecordingGrantRow.recording_id == body.recording_id))
            if existing is not None:
                if existing.recording_sha256 != body.recording_sha256:
                    raise HTTPException(409, 'An immutable source grant already pins a different SHA-256')
                try:
                    with session.begin_nested():
                        session.add(ProjectRecordingGrantRequestRow(request_key=request_key,request_hash=request_hash,grant_id=existing.id))
                        session.flush()
                except IntegrityError as error:
                    retry = session.get(ProjectRecordingGrantRequestRow,request_key)
                    if retry is None or retry.request_hash != request_hash or retry.grant_id != existing.id:
                        raise HTTPException(409,'Concurrent grant request changed its immutable intent') from error
                return _grant_detail(existing)
            manifest = {'schema_version': 'project-recording-grant/1', 'id': uid('source_grant'),
                'project_id': body.project_id, 'recording_id': body.recording_id,
                'recording_sha256': body.recording_sha256, 'created_at': utcnow(), 'note': body.note,
                'source_kind': recording.payload['source_kind'], 'source': recording.payload['source'],
                'source_metadata': recording.payload.get('metadata', []),
                'source_use_scope': recording_source_scope(recording),
                'scope': 'original_source_read_and_supported_sequence_authoring',
                'limitations': ['A source grant does not establish calibration, clock synchronization or commercial rights.']}
            raw = canonical_bytes(manifest)
            row = ProjectRecordingGrantRow(id=manifest['id'], project_id=body.project_id,
                recording_id=body.recording_id, recording_sha256=body.recording_sha256,
                created_at=manifest['created_at'], request_key=request_key, request_hash=request_hash,
                manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest())
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
                    session.add(ProjectRecordingGrantRequestRow(request_key=request_key,request_hash=request_hash,grant_id=row.id))
                    session.flush()
            except IntegrityError as error:
                # A concurrent duplicate cannot overwrite or broaden a grant.
                retry = session.get(ProjectRecordingGrantRequestRow,request_key)
                if retry is not None:
                    if retry.request_hash != request_hash:raise HTTPException(409,'Concurrent grant request changed its immutable intent') from error
                    saved = session.get(ProjectRecordingGrantRow,retry.grant_id)
                    if saved is None:raise HTTPException(409,'Recording grant request lost its immutable grant') from error
                    return _grant_detail(saved)
                saved = session.scalar(select(ProjectRecordingGrantRow).where(
                    ProjectRecordingGrantRow.project_id == body.project_id, ProjectRecordingGrantRow.recording_id == body.recording_id))
                if saved is not None and saved.recording_sha256 == body.recording_sha256:
                    session.add(ProjectRecordingGrantRequestRow(request_key=request_key,request_hash=request_hash,grant_id=saved.id))
                    session.flush()
                    return _grant_detail(saved)
                raise HTTPException(409, 'Concurrent grant conflicts with this immutable request') from error
            return _grant_detail(row)

    @api.get('/project-recordings', tags=['project recordings'])
    def sources(request: Request, project_id: str = 'default', q: str = Query('', max_length=180),
                limit: int = Query(50, ge=1, le=100)):
        principal = project_authorize(request, db, project_id, 'Viewer')
        with db.read_session() as session:
            query = select(RecordingRow).order_by(RecordingRow.created_at.desc(), RecordingRow.id)
            if not (principal.mode != 'project' and project_id == 'default'):
                query = query.join(ProjectRecordingGrantRow, ProjectRecordingGrantRow.recording_id == RecordingRow.id).where(
                    ProjectRecordingGrantRow.project_id == project_id)
            # Match bounded string fields in SQL without loading the whole catalog.
            if q:
                query = query.where(or_(RecordingRow.id.contains(q, autoescape=True),
                    RecordingRow.payload['name'].as_string().contains(q, autoescape=True)))
            rows = list(session.scalars(query.limit(limit + 1)))
            items = []
            for row in rows[:limit]:
                scope = authorize_recording_source(request, db, project_id, row.id)
                summary = RecordingSummary.model_validate({key: value for key, value in row.payload.items()
                                                           if key in RecordingSummary.model_fields}).model_dump(mode='json')
                summary['download_url'] = _download_url(row.id, project_id)
                grant = scope.get('grant')
                scope['grant'] = ({key: grant[key] for key in ('id', 'manifest_hash', 'created_at', 'recording_sha256')}
                                  if grant else None)
                scope['source_use_scope'] = {key: value for key, value in scope['source_use_scope'].items() if key != 'lineage'}
                items.append(summary | scope)
            return {'items': items, 'project_id': project_id, 'has_more': len(rows) > limit,
                    'limit': limit, 'legacy_catalog_exposed': principal.mode != 'project' and project_id == 'default'}

    @api.get('/project-recordings/{recording_id}', tags=['project recordings'])
    def detail(recording_id: str, request: Request, project_id: str = 'default',
               channel_limit: int = Query(128,ge=1,le=256), channel_offset: int = Query(0,ge=0)):
        scope = authorize_recording_source(request, db, project_id, recording_id)
        with db.read_session() as session:
            row = _get_recording(session, recording_id)
            verify_recording_bytes(db, row)
            channels = list(session.scalars(select(RecordingChannelRow).where(RecordingChannelRow.recording_id == recording_id)
                .order_by(RecordingChannelRow.channel_id).offset(channel_offset).limit(channel_limit+1)))
            schema_ids = {channel.schema_ref for channel in channels[:channel_limit] if channel.schema_ref is not None}
            schemas = session.scalars(select(RecordingSchemaRow).where(RecordingSchemaRow.recording_id == recording_id,
                RecordingSchemaRow.schema_id.in_(schema_ids))) if schema_ids else []
            result = dict(row.payload) | {'channels': [channel.payload for channel in channels[:channel_limit]],
                'schemas': [{key:value for key,value in schema.payload.items() if key != 'definition'} for schema in schemas],
                'channel_offset':channel_offset,'channel_limit':channel_limit,'channels_truncated':len(channels)>channel_limit}
            result['download_url'] = _download_url(row.id, project_id)
            return result | scope

    @api.get('/project-recordings/{recording_id}/download', response_class=StreamingResponse,
             tags=['project recordings'], openapi_extra={'x-axiom-min-role': 'Viewer'})
    def download(recording_id: str, request: Request, project_id: str = 'default'):
        authorize_recording_source(request, db, project_id, recording_id)
        with db.read_session() as session:
            row = _get_recording(session, recording_id)
            stream = _verified_recording_stream(db, row)
            size, sha256, identifier = row.payload['size_bytes'], row.sha256, row.id
        def chunks():
            try:
                remaining = size
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise RuntimeError('Original recording shortened during download')
                    remaining -= len(chunk)
                    yield chunk
                if stream.read(1):
                    raise RuntimeError('Original recording grew during download')
            finally:
                stream.close()
        return StreamingResponse(chunks(), media_type='application/octet-stream',
            headers={'Content-Length': str(size), 'X-Content-SHA256': sha256,
                     'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
                     'Content-Disposition': f'attachment; filename="{identifier}.mcap"'},
            background=BackgroundTask(stream.close))

    @api.get('/project-recordings/{recording_id}/samples', response_model=RecordingSamples, tags=['project recordings'])
    def samples(recording_id: str, request: Request, project_id: str = 'default',
                channel_id: int = Query(ge=0, le=65535), at_ns: str | None = Query(None, pattern=r'^(0|[1-9][0-9]{0,19})$'),
                from_start: bool = False, after_ordinal: int | None = Query(None, ge=0),
                from_ordinal: int | None = Query(None, ge=0), limit: int = Query(50, ge=1, le=100)):
        authorize_recording_source(request, db, project_id, recording_id)
        with db.read_session() as session:
            row = _get_recording(session, recording_id)
            verify_recording_bytes(db, row)
            return recording_samples(session, db, recording_id, channel_id, at_ns, limit,
                from_start=from_start, after_ordinal=after_ordinal, from_ordinal=from_ordinal)
