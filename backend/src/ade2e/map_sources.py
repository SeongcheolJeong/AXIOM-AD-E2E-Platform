"""Preserved external map sources, bounded conversion and queued native qualification.

Only the native qualification receipt may publish external executable catalogs.
The CPU converter is optional; it never imports a simulator or changes a world.
"""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from xml.etree import ElementTree as ET
from fastapi import Header, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentExecutionRow, ExperimentArtifactRow
from .experiment_schemas import ExperimentCreate
from .job_models import JobRow
from .map_source_schemas import MAX_UPLOAD_BYTES, MapSourceCreate, MapSourceConvert, MapSourceQualify, MapSourcePublish
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog import register_catalog
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .simulation_catalog_schemas import MapDefinition
from .team_access import project_authorize

SOURCE_KIND = 'external-map-source'
CONVERSION_KIND = 'external-map-conversion'
QUALIFIER = 'carla-map-qualification'
NATIVE_FIDELITY = 'native_carla_external_map_load_readback'


def reference(value):
    return {key: value[key] for key in ('id', 'revision', 'sha256')}


def _blob_path(db, checksum):
    if not re.fullmatch('[a-f0-9]{64}', checksum):
        raise ValueError('Invalid external map blob hash')
    return db.artifact_dir.parent / 'map-sources' / checksum[:2] / checksum


def write_blob(db, raw):
    checksum = hashlib.sha256(raw).hexdigest()
    path = _blob_path(db, checksum)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish only a complete, durable inode. A stopped upload may leave a
    # private temporary file, but never a partial content-addressed blob.
    fd, temporary_name = tempfile.mkstemp(prefix='.' + checksum + '.', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != raw:
                raise HTTPException(409, 'External map blob integrity mismatch')
        else:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return checksum


def read_blob(db, checksum, *, limit=64 * 1024 * 1024):
    path = _blob_path(db, checksum)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise HTTPException(409, 'External map blob is missing or exceeds its bound')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise HTTPException(409, 'External map blob content hash mismatch')
    return raw


def read_record(session, kind, project, identity, revision=None):
    if revision is None:
        revision = session.scalar(select(func.max(Row.revision)).where(
            Row.project_id == project, Row.kind == kind, Row.id == identity))
    row = session.get(Row, (project, kind, identity, revision)) if revision else None
    if row is None:
        raise HTTPException(404, 'External map revision not found in project')
    if hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
        raise HTTPException(409, 'External map manifest integrity mismatch')
    value = json.loads(row.manifest)
    if (value['project_id'], value['id'], value['revision']) != (project, identity, revision):
        raise HTTPException(409, 'External map manifest identity mismatch')
    return value | {'sha256': row.manifest_hash}


def resolve(session, kind, project, ref):
    ref = ref.model_dump() if hasattr(ref, 'model_dump') else ref
    value = read_record(session, kind, project, ref['id'], ref['revision'])
    if value['sha256'] != ref['sha256']:
        raise HTTPException(409, 'External map pinned revision hash differs')
    return value


def resolve_conversion(db, project, source_ref, conversion_ref):
    with db.read_session() as session:
        source = resolve(session, SOURCE_KIND, project, source_ref)
        conversion = resolve(session, CONVERSION_KIND, project, conversion_ref)
    if conversion['source_ref'] != reference(source):
        raise HTTPException(409, 'Conversion belongs to a different source revision')
    read_blob(db, source['source_sha256'], limit=MAX_UPLOAD_BYTES)
    for attachment in source.get('attachments', []):
        read_blob(db, attachment['sha256'], limit=MAX_UPLOAD_BYTES)
    read_blob(db, conversion['report_sha256'], limit=32 * 1024 * 1024)
    if not conversion['ready'] or not conversion.get('candidate_sha256'):
        raise HTTPException(422, 'Conversion is blocked; inspect retained diagnostics')
    raw = read_blob(db, conversion['candidate_sha256'], limit=20 * 1024 * 1024)
    return source, conversion, raw


def _decode(value, *, limit=MAX_UPLOAD_BYTES):
    # Strict base64 has a predictable decoded size; reject before allocating
    # bytes that would exceed the remaining source-plus-attachment budget.
    padding = len(value) - len(value.rstrip('='))
    if len(value) // 4 * 3 - min(padding, 2) > limit:
        raise HTTPException(422, 'Source and attachments together exceed 32 MiB')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise HTTPException(422, 'Map upload must be strict base64') from error
    if not 0 < len(raw) <= limit:
        raise HTTPException(422, 'Map upload exceeds 32 MiB or is empty')
    return raw


def _filename(name):
    if '/' in name or '\\' in name or name in {'.', '..'} or any(ord(c) < 32 for c in name):
        raise HTTPException(422, 'Upload filename must be a plain filename')
    return name


def save_source(db, body):
    encoded = [body.content_base64, *(item.content_base64 for item in body.attachments)]
    # Each independently encoded attachment may contribute padding overhead.
    max_encoded_bytes = 4 * ((MAX_UPLOAD_BYTES + 2) // 3) + 4 * (len(encoded) - 1)
    if sum(len(value) for value in encoded) > max_encoded_bytes:
        raise HTTPException(422, 'Source and attachments together exceed 32 MiB')
    raw = _decode(body.content_base64, limit=MAX_UPLOAD_BYTES)
    remaining = MAX_UPLOAD_BYTES - len(raw)
    attachments = []
    for item in body.attachments:
        data = _decode(item.content_base64, limit=remaining)
        remaining -= len(data)
        attachments.append((item, data))
    if len(canonical_bytes(body.metadata)) > 65536:
        raise HTTPException(422, 'Source metadata exceeds 64 KiB')
    definition = dict(name=body.name, source_kind=body.source_kind, filename=_filename(body.filename),
        source_sha256=write_blob(db, raw), size_bytes=len(raw), rights=body.rights.model_dump(mode='json'),
        metadata=body.metadata, attachments=[dict(filename=_filename(item.filename), role=item.role,
            sha256=write_blob(db, data), size_bytes=len(data)) for item, data in attachments])
    identity = body.id or uid('mapsource')
    with jobs._write(db) as session:
        current = session.scalar(select(func.max(Row.revision)).where(
            Row.project_id == body.project_id, Row.kind == SOURCE_KIND, Row.id == identity)) or 0
        if current:
            old = read_record(session, SOURCE_KIND, body.project_id, identity, current)
            if all(old.get(key) == value for key, value in definition.items()) and body.expected_revision in {current, current - 1}:
                return old
        if current != body.expected_revision:
            raise HTTPException(409, 'Source changed; reopen its latest revision')
        value = dict(schema_version='map-source/1', id=identity, revision=current + 1, project_id=body.project_id,
                     **definition, editable=False, native_ready=False, created_at=utcnow())
        session.add(Row(project_id=body.project_id, kind=SOURCE_KIND, id=identity, revision=current + 1,
                        manifest=canonical_bytes(value), manifest_hash=digest(value)))
        return value | {'sha256': digest(value)}


def converter_capabilities():
    try:
        from axiom_geospatial import capabilities
        ngii = capabilities()
    except (ImportError, ModuleNotFoundError):
        ngii = {'available': False, 'reason': 'Optional axiom_geospatial adapter is not installed'}
    return {'opendrive': {'available': True, 'profile': 'preserved_external_xodr/1'}, 'ngii_shp': ngii}


def _opendrive(raw, options):
    if options:
        raise ValueError('OpenDRIVE is preserved in its local coordinate frame; no implicit geometry conversion options')
    text = raw.decode('utf-8-sig')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
        raise ValueError('DTD/entity declarations are prohibited')
    root = ET.fromstring(text)
    if root.tag != 'OpenDRIVE' or not root.findall('road'):
        raise ValueError('Expected an OpenDRIVE document with roads')
    if len(root.findall('road')) > 1024:
        raise ValueError('Pilot OpenDRIVE exceeds 1024 roads; crop the source ROI')
    # UTF-8 source bytes remain separate. ASCII numeric references preserve
    # names across the pinned CARLA build without silently rewriting geometry.
    candidate = text.encode('ascii', 'xmlcharrefreplace').decode('ascii')
    if len(candidate.encode()) > 20 * 1024 * 1024:
        raise ValueError('Execution OpenDRIVE exceeds 20 MiB')
    return dict(ready=True, xodr_text=candidate, diagnostics=[dict(severity='info', code='native_not_checked',
        message='XML parsed; native road mesh, topology and driving are not yet verified')],
        normalized_network=None, coordinate_transform={'mode': 'already_local_opendrive', 'units': 'm'},
        inference_flags=[], fidelity='external_opendrive_not_executed',
        converter={'id': 'axiom.external-opendrive', 'version': '1',
                   'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def convert_source(db, identity, body):
    with db.read_session() as session:
        source = resolve(session, SOURCE_KIND, body.project_id, dict(id=identity, revision=body.revision, sha256=body.sha256))
    if len(canonical_bytes(body.options)) > 65536:
        raise HTTPException(422, 'Conversion options exceed 64 KiB')
    raw = read_blob(db, source['source_sha256'], limit=MAX_UPLOAD_BYTES)
    try:
        if source['source_kind'] == 'opendrive':
            result = _opendrive(raw, body.options)
        else:
            try:
                from axiom_geospatial import inspect_ngii_zip
            except ImportError as error:
                raise HTTPException(503, 'Optional NGII converter is unavailable; original source remains preserved') from error
            result = inspect_ngii_zip(raw, source_name=source['name'], options=body.options)
            if hasattr(result, 'model_dump'):
                result = result.model_dump(mode='json')
    except (ValueError, UnicodeError, ET.ParseError) as error:
        result = dict(ready=False, diagnostics=[dict(severity='error', code='conversion_rejected', message=str(error))],
                      xodr_text=None, inference_flags=[], coordinate_transform={},
                      converter={'id': 'axiom.external-map', 'version': '1'}, fidelity='preserved_source_not_executed')
    text = result.pop('xodr_text', None)
    if result.get('source_sha256', source['source_sha256']) != source['source_sha256']:
        raise HTTPException(409, 'Converter result source hash differs from preserved upload')
    ready = bool(result.get('ready') and text and not any(d.get('severity') == 'error' for d in result.get('diagnostics', [])))
    if text and len(text.encode()) > 20 * 1024 * 1024:
        raise HTTPException(422, 'Converted OpenDRIVE exceeds 20 MiB')
    candidate_hash = write_blob(db, text.encode()) if ready else None
    report = result | {'source_ref': reference(source), 'source_sha256': source['source_sha256'],
                       'options': body.options, 'ready': ready, 'candidate_sha256': candidate_hash}
    report_bytes = canonical_bytes(report)
    if len(report_bytes) > 32 * 1024 * 1024:
        raise HTTPException(422, 'Conversion report exceeds 32 MiB')
    report_hash = write_blob(db, report_bytes)
    conversion_id = 'mapconv_' + digest({'source': reference(source), 'report': report_hash})[:32]
    value = dict(schema_version='map-conversion/1', id=conversion_id, revision=1, project_id=body.project_id,
        source_ref=reference(source), source_sha256=source['source_sha256'], candidate_sha256=candidate_hash,
        report_sha256=report_hash, options=body.options, ready=ready, editable=False, native_ready=False,
        diagnostics=result.get('diagnostics', []), coordinate_transform=result.get('coordinate_transform') or {},
        inference_flags=result.get('inference_flags', []), converter=result.get('converter', {}),
        fidelity=(result['fidelity'].get('kind', 'converted_road_not_executed') if isinstance(result.get('fidelity'), dict)
                  else result.get('fidelity', 'converted_road_not_executed')))
    with jobs._write(db) as session:
        existing = session.get(Row, (body.project_id, CONVERSION_KIND, conversion_id, 1))
        if existing:
            return read_record(session, CONVERSION_KIND, body.project_id, conversion_id, 1)
        value['created_at'] = utcnow()
        session.add(Row(project_id=body.project_id, kind=CONVERSION_KIND, id=conversion_id, revision=1,
                        manifest=canonical_bytes(value), manifest_hash=digest(value)))
    return value | {'sha256': digest(value)}


def qualification_artifact(db, session, execution_id, name):
    artifact = session.scalar(select(ExperimentArtifactRow).where(
        ExperimentArtifactRow.job_id == execution_id, ExperimentArtifactRow.name == name))
    if artifact is None:
        raise HTTPException(409, 'Native qualification artifact is missing')
    return experiment_jobs.artifact_path(db, artifact).read_bytes()


def publish_map(db, identity, body):
    source_ref = dict(id=identity, revision=body.revision, sha256=body.sha256)
    source, conversion, candidate = resolve_conversion(db, body.project_id, source_ref, body.conversion_ref)
    with db.read_session() as session:
        row = session.scalar(select(JobRow).where(JobRow.id == body.execution_id))
        subject = session.get(ExperimentExecutionRow, body.execution_id)
        if row is None or subject is None or subject.project_id != body.project_id or row.adapter_id != QUALIFIER:
            raise HTTPException(404, 'Native map qualification not found in project')
        detail = experiment_jobs.detail(session, row)
        pinned = row.prepared['input']
        if (pinned.get('source_ref') != source_ref or pinned.get('conversion_ref') != reference(conversion)
                or detail.status != 'completed' or detail.outcome_uncertain or detail.fidelity != NATIVE_FIDELITY):
            raise HTTPException(409, 'Only a completed matching native map qualification may publish')
        receipt = json.loads(qualification_artifact(db, session, row.id, 'map-qualification.json'))
        definition = json.loads(qualification_artifact(db, session, row.id, 'map-catalog.json'))
        if (receipt.get('schema_version') != 'carla-map-qualification/1' or receipt.get('status') != 'completed'
                or receipt.get('cleanup_status') != 'confirmed' or receipt.get('source_ref') != source_ref
                or receipt.get('conversion_ref') != reference(conversion)
                or receipt.get('input_opendrive_sha256') != conversion['candidate_sha256']
                or receipt.get('native_opendrive_sha256') != conversion['candidate_sha256']
                or receipt.get('map_catalog_sha256') != hashlib.sha256(canonical_bytes(definition)).hexdigest()
                or receipt.get('physical_validation') is not False):
            raise HTTPException(409, 'Native qualification provenance differs from pinned candidate')
        definition.update(name=body.name or source['name'], native_name='external__' + conversion['candidate_sha256'][:16],
            rights=source['rights'], external=dict(source=source_ref, conversion=reference(conversion),
                qualification_execution_id=row.id, qualification_manifest_hash=detail.manifest_hash,
                input_opendrive_sha256=conversion['candidate_sha256'],
                native_opendrive_sha256=receipt['native_opendrive_sha256'],
                coordinate_transform=conversion['coordinate_transform']))
        checked = MapDefinition.model_validate(definition)
        if checked.opendrive.content.encode() != candidate or checked.server_version != pinned['server_version']:
            raise HTTPException(409, 'Native catalog differs from exact execution bytes or engine version')
    catalog_id = 'external-' + digest({'source': source_ref, 'conversion': reference(conversion), 'execution': body.execution_id})[:40]
    result = register_catalog(db, 'map', dict(project_id=body.project_id, id=catalog_id,
        expected_revision=0, definition=checked.model_dump(mode='json')))
    return dict(map=result, map_ref=reference(result), native_ready=True,
                qualification_scope='native_map_load_readback_not_driving', editable=False)


def register_map_source_routes(api, db, registry):
    @api.get('/map-sources/capabilities', tags=['External maps'])
    def capabilities(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        handler = registry.get(QUALIFIER)
        return dict(schema_version='map-source-capabilities/1', source_kinds=['opendrive', 'ngii_shp'],
            max_upload_bytes=MAX_UPLOAD_BYTES, editable=False, conversion=converter_capabilities(),
            qualification=dict(adapter_id=QUALIFIER, configured=bool(handler and handler.metadata().status == 'available'),
                readiness='not_probed', scope='native_map_load_readback_not_driving'),
            limits=dict(native_lane_nodes=50000, local_coordinate_abs_m=10000, xodr_bytes=20*1024*1024))

    @api.post('/map-sources', status_code=201, tags=['External maps'])
    def create(body: MapSourceCreate, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        return save_source(db, body)

    @api.get('/map-sources', tags=['External maps'])
    def listing(request: Request, project_id: str = 'default', limit: int = Query(100, ge=1, le=1000),
                offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = list(session.execute(select(Row.id, func.max(Row.revision)).where(
                Row.project_id == project_id, Row.kind == SOURCE_KIND).group_by(Row.id).order_by(Row.id)
                .limit(limit + 1).offset(offset)))
            return {'items': [read_record(session, SOURCE_KIND, project_id, identity, revision) for identity, revision in rows[:limit]],
                    'next_offset': offset + limit if len(rows) > limit else None}

    @api.get('/map-sources/{identity}/revisions', tags=['External maps'])
    def revisions(identity: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == SOURCE_KIND,
                Row.id == identity).order_by(Row.revision.desc()).limit(1000))
            return {'items': [read_record(session, SOURCE_KIND, project_id, identity, row.revision) for row in rows]}

    @api.get('/map-sources/{identity}', tags=['External maps'])
    def read(identity: str, request: Request, project_id: str = 'default', revision: int | None = Query(None, ge=1)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            source = read_record(session, SOURCE_KIND, project_id, identity, revision)
            conversions = [read_record(session, CONVERSION_KIND, project_id, row.id, row.revision) for row in session.scalars(
                select(Row).where(Row.project_id == project_id, Row.kind == CONVERSION_KIND)) if json.loads(row.manifest).get('source_ref') == reference(source)]
            rows = session.scalars(select(JobRow).join(ExperimentExecutionRow, ExperimentExecutionRow.job_id == JobRow.id).where(
                ExperimentExecutionRow.project_id == project_id, JobRow.adapter_id == QUALIFIER).order_by(JobRow.created_at.desc()).limit(100))
            executions = [experiment_jobs.detail(session, row).model_dump(mode='json') for row in rows if row.prepared['input'].get('source_ref') == reference(source)]
            published = []
            for row in session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == 'map')):
                value = json.loads(row.manifest)
                if value.get('definition', {}).get('external', {}).get('source') == reference(source):
                    from .simulation_catalog import get_catalog
                    published.append(reference(get_catalog(session, 'map', project_id, row.id, row.revision)))
        return source | dict(conversions=conversions, executions=executions, published_maps=published,
            native_ready=bool(published), qualification_scope='native_map_load_readback_not_driving')

    @api.get('/map-sources/{identity}/export', tags=['External maps'])
    def export(identity: str, request: Request, project_id: str = 'default', revision: int | None = Query(None, ge=1),
               attachment: int | None = Query(None, ge=0, le=7)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            source = read_record(session, SOURCE_KIND, project_id, identity, revision)
        if attachment is not None:
            if attachment >= len(source['attachments']):
                raise HTTPException(404, 'Source attachment not found')
            checksum = source['attachments'][attachment]['sha256']
        else:
            checksum = source['source_sha256']
        return Response(read_blob(db, checksum, limit=MAX_UPLOAD_BYTES), media_type='application/octet-stream',
                        headers={'X-Content-SHA256': checksum})

    @api.post('/map-sources/{identity}/convert', tags=['External maps'])
    def convert(identity: str, body: MapSourceConvert, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        return convert_source(db, identity, body)

    @api.get('/map-sources/{identity}/conversions/{conversion_id}/export', tags=['External maps'])
    def export_conversion(identity: str, conversion_id: str, request: Request, project_id: str = 'default',
                          format: str = Query('report', pattern='^(report|opendrive)$')):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            conversion = read_record(session, CONVERSION_KIND, project_id, conversion_id, 1)
            if conversion['source_ref']['id'] != identity:
                raise HTTPException(404, 'Conversion does not belong to source')
        checksum = conversion['report_sha256'] if format == 'report' else conversion['candidate_sha256']
        if not checksum:
            raise HTTPException(409, 'Blocked conversion has no execution OpenDRIVE')
        return Response(read_blob(db, checksum), media_type='application/json' if format == 'report' else 'application/xml',
                        headers={'X-Content-SHA256': checksum})

    @api.post('/map-sources/{identity}/qualify', status_code=202, tags=['External maps'])
    def qualify(identity: str, body: MapSourceQualify, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        project_authorize(request, db, body.project_id, 'Engineer')
        source_ref = dict(id=identity, revision=body.revision, sha256=body.sha256)
        source, conversion, _ = resolve_conversion(db, body.project_id, source_ref, body.conversion_ref)
        spec = ExperimentCreate(project_id=body.project_id, name=source['name'][:145] + ' · native map qualification',
            kind='object_sim', adapter_id=QUALIFIER, input=dict(source_ref=source_ref,
                conversion_ref=reference(conversion), server_version=body.server_version))
        experiment = experiment_jobs.create(db, registry, spec, 'map-qualify-' + idempotency_key)
        return experiment_jobs.submit(db, registry, experiment['id'], experiment['preview_hash'], 'map-qualify-' + idempotency_key)

    @api.post('/map-sources/{identity}/publish', status_code=201, tags=['External maps'])
    def publish(identity: str, body: MapSourcePublish, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        return publish_map(db, identity, body)
