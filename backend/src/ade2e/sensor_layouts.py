"""Offline placement revisions, atomic normal-queue runs and retained observations."""
import hashlib
import json
import math
import zipfile
from fastapi import Header, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentArtifactRow, ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .job_models import JobRow
from .sensor_layout_schemas import (ADAPTER, VERSION, FIDELITY, SENSOR_KEYS, SensorLayoutAction,
    SensorLayoutExecute, SensorLayoutObservationCreate, SensorLayoutSave, baseline_rig, browser_numbers, placement_rig)
from .driving_sensor_capture_schemas import DrivingSensorCaptureSpec
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

LAYOUT = 'sensor-layout'
LINK = 'sensor-layout-link'
OBSERVATION = 'sensor-layout-note'  # Catalog kind is limited to 20 characters.
WRITE = 'sensor-layout-write'
SCHEMAS = {LAYOUT: 'sensor-layout/1', LINK: 'sensor-layout-execution/1',
           OBSERVATION: 'sensor-layout-observation/1', WRITE: 'sensor-layout-write/1'}
LIMITATIONS = ['CARLA engine sensor model; not measured physical sensor validation',
    'Fixed reference controller does not use camera, LiDAR or radar to drive',
    'Only four sensor mounts may change; trajectory, capture parameters and baseline remain fixed']
EXECUTION_FIELDS = ('id', 'experiment_id', 'project_id', 'status', 'experiment_kind', 'input_hash',
    'manifest_hash', 'adapter_id', 'adapter_version', 'fidelity', 'outcome_uncertain',
    'created_at', 'started_at', 'finished_at', 'failure_http_status')
TASKS = [dict(id='camera_direction', title='카메라 방향 비교', sensor_key='front', rotation_deg=[0, -15, 0],
              description='B 전방 카메라 yaw를 -15도로 바꾸고 같은 시간의 A/B 영상을 비교합니다.'),
         dict(id='lidar_height', title='LiDAR 높이 비교', sensor_key='lidar', translation_m=[1.5, 0, 2.5],
              description='B LiDAR 높이를 2.5m로 바꾸고 같은 시간의 포인트 분포를 관찰합니다.'),
         dict(id='radar_direction', title='Radar 방향 비교', sensor_key='radar', rotation_deg=[0, 15, 0],
              description='B Radar yaw를 15도로 바꾸고 반환점 방향을 관찰합니다.')]


def _template():
    value = dict(id='standard_driving_20s', revision=1, rig_a=baseline_rig(), rig_b=baseline_rig(),
                 seed=42, adapter_id=ADAPTER, adapter_version=VERSION)
    return {**value, 'sha256': digest(value)}


def _ref(value):
    return {key: value[key] for key in ('id', 'revision', 'sha256')}


def _read(row):
    try:
        if digest(json.loads(row.manifest)) != row.manifest_hash or hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
            raise ValueError('hash')
        value = json.loads(row.manifest)
        if (value['project_id'], value['id'], value['revision'], value['schema_version']) != (
                row.project_id, row.id, row.revision, SCHEMAS[row.kind]):
            raise ValueError('identity')
        if row.kind == LAYOUT:
            if (value['rig_a'] != baseline_rig() or value['rig_b'] != placement_rig(value['rig_b'])
                    or value['seed'] != 42 or value['adapter_id'] != ADAPTER or value['adapter_version'] != VERSION
                    or value['template_ref'] != _ref(_template())):
                raise ValueError('fixed template')
        return {**value, 'sha256': row.manifest_hash}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, 'Sensor layout record failed its integrity check') from exc


def _get(session, project, kind, identity, revision=None):
    if revision is None:
        revision = session.scalar(select(func.max(Row.revision)).where(
            Row.project_id == project, Row.kind == kind, Row.id == identity))
    row = session.get(Row, (project, kind, identity, revision)) if revision else None
    if row is None:
        raise HTTPException(404, 'Sensor layout record not found in project')
    return _read(row)


def _put(session, project, kind, value):
    raw = canonical_bytes(value)
    row = Row(project_id=project, kind=kind, id=value['id'], revision=value['revision'],
              manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest())
    session.add(row)
    session.flush()
    return _read(row)


def _values(session, project, kind):
    latest = select(Row.id, func.max(Row.revision).label('revision')).where(
        Row.project_id == project, Row.kind == kind).group_by(Row.id).subquery()
    rows = session.scalars(select(Row).join(latest, (Row.id == latest.c.id) & (Row.revision == latest.c.revision)).where(
        Row.project_id == project, Row.kind == kind))
    return sorted((_read(row) for row in rows), key=lambda item: (item['created_at'], item['id']), reverse=True)


def _layout(session, project, ref, identity=None):
    ref = ref.model_dump(mode='json') if hasattr(ref, 'model_dump') else ref
    if identity is not None and ref['id'] != identity:
        raise HTTPException(409, 'Layout URL and requested reference differ')
    value = _get(session, project, LAYOUT, ref['id'], ref['revision'])
    if value['sha256'] != ref['sha256']:
        raise HTTPException(409, 'Saved sensor layout revision hash differs')
    return value


def _operation(session, project, scope, key, body):
    identity = 'write_' + digest([project, scope, key])[:48]
    request_hash = digest(browser_numbers(body.model_dump(mode='json')))
    row = session.get(Row, (project, WRITE, identity, 1))
    if row is None:
        return identity, request_hash, None
    prior = _read(row)
    if prior['request_hash'] != request_hash:
        raise HTTPException(409, 'Idempotency key already belongs to another sensor layout request')
    target = prior['target']
    return identity, request_hash, _get(session, project, target['kind'], target['id'], target['revision'])


def _remember(session, project, operation, request_hash, kind, value):
    _put(session, project, WRITE, dict(schema_version=SCHEMAS[WRITE], id=operation, revision=1,
        project_id=project, created_at=utcnow(), request_hash=request_hash,
        target=dict(kind=kind, id=value['id'], revision=value['revision'])))


def _definition(layout):
    return ExperimentCreate(project_id=layout['project_id'], name=layout['name'], kind='sensor_capture',
        adapter_id=ADAPTER, adapter_version=VERSION, seed=42, comparison_kind='same_scene_sensor_ab',
        input=browser_numbers(dict(rig_a=layout['rig_a'], rig_b=layout['rig_b'])))


def _rig_snapshot(rig):
    model = DrivingSensorCaptureSpec.model_validate(rig)
    frozen = model.model_dump(mode='json')
    frozen['seed'] = 42
    for value, sensor in zip(frozen['sensors'], model.sensors):
        value['native_parameters'] = sensor.native_parameters()
    return frozen


def _snapshot(snapshot, layout):
    if (snapshot.get('schema_version') != 'carla-driving-multisensor-experiment/1'
            or snapshot.get('comparison_kind') != 'same_scene_sensor_ab'
            or snapshot.get('rigs') != {key: _rig_snapshot(layout[key]) for key in ('rig_a', 'rig_b')}):
        raise HTTPException(409, 'Prepared sensors differ from the exact saved layout revision')


def _changes(layout):
    return [dict(sensor_key=a['key'], field=field, baseline=a[field], value=b[field])
        for a, b in zip(layout['rig_a']['sensors'], layout['rig_b']['sensors'])
        for field in ('translation_m', 'rotation_deg') if a[field] != b[field]]


def _prepare(registry, layout):
    if not _changes(layout):
        raise HTTPException(422, 'Change at least one B sensor mount before previewing this layout')
    prepared = experiment_jobs.prepare(registry, _definition(layout))
    payload = prepared.to_payload()
    if payload['handler']['fidelity'] != FIDELITY:
        raise HTTPException(422, 'Placement beta requires the native driving multisensor adapter fidelity')
    _snapshot(payload['input'], layout)
    return prepared


def _execution(session, project, identity, layout=None):
    subject = session.get(ExperimentExecutionRow, identity)
    job = session.scalar(select(JobRow).where(JobRow.id == identity))
    if subject is None or subject.project_id != project or job is None:
        raise HTTPException(404, 'Sensor layout execution not found in project')
    detail = experiment_jobs.detail(session, job)
    spec, snapshot = job.prepared['spec'], job.prepared['input']
    if (spec['kind'] != 'sensor_capture' or spec['adapter_id'] != ADAPTER or spec['adapter_version'] != VERSION
            or spec['comparison_kind'] != 'same_scene_sensor_ab' or spec['seed'] != 42
            or job.prepared['handler']['fidelity'] != FIDELITY):
        raise HTTPException(422, 'A pinned native driving placement A/B execution is required')
    if layout is None:
        try:
            if set(spec['input']) != {'rig_a', 'rig_b'} or spec['input']['rig_a'] != baseline_rig():
                raise ValueError('baseline')
            layout = dict(project_id=project, name=spec['name'], rig_a=baseline_rig(), rig_b=placement_rig(spec['input']['rig_b']))
        except (TypeError, KeyError, ValueError) as exc:
            raise HTTPException(422, 'Execution does not use the fixed placement profile') from exc
    expected = _definition(layout).model_dump(mode='json')
    if any(spec.get(key) != value for key, value in expected.items() if key != 'name'):
        raise HTTPException(409, 'Execution inputs differ from the saved sensor layout revision')
    _snapshot(snapshot, layout)
    if detail.status == 'completed':
        summary = detail.summary or {}
        if detail.fidelity != FIDELITY or summary.get('schema_version') != 'sensor-experiment-result/1' or set(summary.get('captures', {})) != {'rig_a', 'rig_b'}:
            raise HTTPException(409, 'Completed placement execution lacks its native A/B capture summary')
        for side, rig in snapshot['rigs'].items():
            capture = summary['captures'][side]
            if (capture.get('configuration_hash') != digest(rig) or capture.get('frame_count') != 200
                    or capture.get('sensor_count') != 4 or capture.get('cleanup_status') != 'confirmed' or capture.get('drop_count') != 0):
                raise HTTPException(409, 'Completed capture summary differs from its pinned placement rig')
    return detail.model_dump(mode='json'), snapshot


def _execution_metadata(execution):
    return {key: execution.get(key) for key in EXECUTION_FIELDS}


def _links(session, project, identity, revision=None):
    return [value for value in _values(session, project, LINK)
        if value['layout_ref']['id'] == identity and (revision is None or value['layout_ref']['revision'] == revision)]


def _link_result(session, project, link):
    layout = _layout(session, project, link['layout_ref'])
    execution, _ = _execution(session, project, link['execution_id'], layout)
    if link['input_hash'] != execution['input_hash']:
        raise HTTPException(409, 'Layout link and execution input hashes differ')
    return {**link, 'execution': execution}


def _observations(session, project, execution_id=None, layout_id=None, revision=None):
    return [value for value in _values(session, project, OBSERVATION)
        if (execution_id is None or value['execution_id'] == execution_id)
        and (layout_id is None or value['layout_ref']['id'] == layout_id)
        and (revision is None or value['layout_ref']['revision'] == revision)]


def _samples(db, session, execution, snapshot, sensor_key, side, time_s):
    if execution['status'] != 'completed' or execution['outcome_uncertain']:
        raise HTTPException(422, 'Observations require a completed, certain native placement execution')
    samples = {}
    # The product timeline starts at the first retained frame, not world time zero.
    index = min(199, math.floor(time_s * 10 + 1e-8))
    for stage in (('rig_a', 'rig_b') if side == 'both' else (side,)):
        names = {name: list(session.scalars(select(ExperimentArtifactRow).where(
            ExperimentArtifactRow.job_id == execution['id'], ExperimentArtifactRow.name == stage + '-' + name)))
            for name in ('capture-manifest.json', 'native-capture.zip')}
        if any(len(rows) != 1 for rows in names.values()):
            raise HTTPException(409, 'Placement observation requires the exact retained manifest and archive')
        manifest_row, archive_row = (names[name][0] for name in ('capture-manifest.json', 'native-capture.zip'))
        if (manifest_row.media_type != 'application/json' or manifest_row.size_bytes > 8 * 1024**2
                or archive_row.media_type != 'application/zip' or archive_row.size_bytes > 780 * 1024**2):
            raise HTTPException(409, 'Retained placement artifact exceeds its supported type or size')
        summary = execution['summary']['captures'][stage]
        if summary.get('archive_sha256') != archive_row.sha256:
            raise HTTPException(409, 'Placement archive differs from its sealed capture summary')
        manifest_path = experiment_jobs.artifact_path(db, manifest_row)
        archive_path = experiment_jobs.artifact_path(db, archive_row)
        try:
            raw = manifest_path.read_bytes()
            manifest = json.loads(raw)
            rig = snapshot['rigs'][stage]
            if (manifest['schema_version'] != 'carla-driving-multisensor-result/1' or manifest['adapter_version'] != VERSION
                    or manifest['configuration_hash'] != digest(rig) or manifest['rig_revision'] != rig['rig_revision']
                    or manifest['seed'] != 42 or manifest['status'] != 'completed' or manifest['fidelity'] != FIDELITY
                    or manifest['external_execution_id'] != summary['external_execution_id'] or manifest['frame_count'] != 200):
                raise ValueError('manifest identity')
            records = manifest['records']
            if len(records) != 800:
                raise ValueError('frame denominator')
            first = records[0]
            selected = [record for record in records if record.get('key') == sensor_key and record.get('index') == index]
            if len(selected) != 1:
                raise ValueError('frame identity')
            record = selected[0]
            stamp = record['timestamp_seconds']
            if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                    or type(record['frame']) is not int or record['frame'] != first['frame'] + index
                    or abs(stamp - first['timestamp_seconds'] - index / 10) > 1e-5
                    or record['simulation_timestamp_ns'] != str(round(stamp * 1e9))):
                raise ValueError('frame clock')
            descriptor = record['raw_artifact']
            kind = next(sensor['kind'] for sensor in rig['sensors'] if sensor['key'] == sensor_key)
            expected_name = f'frames/{index:04d}-{sensor_key}.' + ('bgra8' if kind == 'rgb' else 'f32')
            if descriptor['name'] != expected_name or descriptor not in manifest['artifacts']:
                raise ValueError('raw frame descriptor')
            with zipfile.ZipFile(archive_path) as archive:
                if len(archive.infolist()) > 1210 or len(set(archive.namelist())) != len(archive.infolist()):
                    raise ValueError('archive bounds or duplicate')
                info = archive.getinfo('capture-manifest.json')
                if info.file_size != len(raw) or archive.read(info) != raw:
                    raise ValueError('archive manifest')
                info = archive.getinfo(expected_name)
                if info.file_size != descriptor['size_bytes'] or info.file_size > 640 * 360 * 4 or info.compress_type != zipfile.ZIP_STORED:
                    raise ValueError('raw frame size')
                content = archive.read(info)
                if hashlib.sha256(content).hexdigest() != descriptor['sha256']:
                    raise ValueError('raw frame hash')
            samples[stage] = dict(status='observed', manifest_artifact_id=manifest_row.id, manifest_sha256=manifest_row.sha256,
                archive_artifact_id=archive_row.id, archive_sha256=archive_row.sha256, sensor_key=sensor_key,
                index=index, frame=record['frame'], time_s=index / 10, timestamp_seconds=stamp,
                simulation_timestamp_ns=record['simulation_timestamp_ns'], artifact=descriptor['name'], sha256=descriptor['sha256'])
        except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile) as exc:
            raise HTTPException(409, 'Retained placement frame reference failed its integrity check') from exc
    return samples


def _page(values, limit, offset, schema):
    return dict(schema_version=schema, items=values[offset:offset + limit],
                next_offset=offset + limit if len(values) > offset + limit else None)


def register_sensor_layout_routes(api, db, registry):
    @api.get('/sensor-sim/layouts/templates', tags=['sensor layouts'])
    def templates(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return dict(schema_version='sensor-layout-templates/1', template=_template(),
            bounds=dict(translation_m=dict(min=-10, max=10, step=.05), rotation_deg=dict(min=-180, max=180, step=1),
                        time_s=dict(min=0, max=19.9, step=.1)), tasks=TASKS, limitations=LIMITATIONS)

    @api.get('/sensor-sim/layouts', tags=['sensor layouts'])
    def layouts(request: Request, project_id: str = 'default', limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return _page(_values(session, project_id, LAYOUT), limit, offset, 'sensor-layouts/1')

    @api.post('/sensor-sim/layouts', status_code=201, tags=['sensor layouts'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def save(body: SensorLayoutSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, request_hash, prior = _operation(session, body.project_id, 'save', idempotency_key, body)
            if prior:
                return prior
            previous = _get(session, body.project_id, LAYOUT, body.id) if body.id else None
            current = previous['revision'] if previous else 0
            if current != body.expected_revision:
                raise HTTPException(409, 'Sensor layout revision changed; reload before saving')
            copied = body.copied_from.model_dump(mode='json') if body.copied_from else (previous or {}).get('copied_from')
            if copied:
                _layout(session, body.project_id, copied)
            value = dict(schema_version=SCHEMAS[LAYOUT], id=body.id or uid('layout'), project_id=body.project_id,
                revision=current + 1, name=body.name, note=body.note, rig_a=baseline_rig(), rig_b=placement_rig(body.rig_b),
                seed=42, adapter_id=ADAPTER, adapter_version=VERSION, template_ref=_ref(_template()), copied_from=copied,
                created_at=utcnow(), author=dict(user_label=principal.user_label, role=principal.role))
            result = _put(session, body.project_id, LAYOUT, value)
            _remember(session, body.project_id, operation, request_hash, LAYOUT, result)
            return result

    @api.get('/sensor-sim/layouts/{identity}', tags=['sensor layouts'])
    def get_layout(identity: str, request: Request, project_id: str = 'default', revision: int | None = Query(None, ge=1)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return _get(session, project_id, LAYOUT, identity, revision)

    @api.get('/sensor-sim/layouts/{identity}/revisions', tags=['sensor layouts'])
    def revisions(identity: str, request: Request, project_id: str = 'default', limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            _get(session, project_id, LAYOUT, identity)
            rows = session.scalars(select(Row).where(Row.project_id == project_id, Row.kind == LAYOUT, Row.id == identity).order_by(Row.revision.desc()))
            return _page([_read(row) for row in rows], limit, offset, 'sensor-layout-revisions/1')

    @api.post('/sensor-sim/layouts/{identity}/preview', tags=['sensor layouts'],
              openapi_extra={'x-axiom-min-role': 'Engineer', 'x-axiom-read-only': True})
    def preview(identity: str, body: SensorLayoutAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        with db.read_session() as session:
            layout = _layout(session, body.project_id, body.layout_ref, identity)
        prepared = _prepare(registry, layout)
        payload = prepared.to_payload()
        return dict(schema_version='sensor-layout-preview/1', project_id=body.project_id, layout_ref=_ref(layout),
            experiment=_definition(layout).model_dump(mode='json'), changes=_changes(layout),
            preview=dict(preview_hash=prepared.input_hash, prepared=payload, job_count=1,
                resource_id=payload['handler']['resource_id'], execution_fidelity=FIDELITY,
                requires_explicit_submit=True, evaluation_status='not_evaluated'))

    @api.post('/sensor-sim/layouts/{identity}/executions', status_code=202, tags=['sensor layouts'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def execute(identity: str, body: SensorLayoutExecute, request: Request,
                idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        scope = 'execute:' + identity
        with db.read_session() as session:
            _, _, prior = _operation(session, body.project_id, scope, idempotency_key, body)
            if prior:
                return _link_result(session, body.project_id, prior)
            layout = _layout(session, body.project_id, body.layout_ref, identity)
        prepared = _prepare(registry, layout)
        if prepared.input_hash != body.preview_hash:
            raise HTTPException(409, 'Native preview changed; review a fresh preview before submitting')
        with jobs._write(db) as session:
            operation, request_hash, prior = _operation(session, body.project_id, scope, idempotency_key, body)
            if prior:
                return _link_result(session, body.project_id, prior)
            _layout(session, body.project_id, body.layout_ref, identity)
            experiment = experiment_jobs.create_prepared_row(session, prepared, operation)
            session.flush()
            execution = experiment_jobs.submit_row(session, registry, experiment['id'], body.preview_hash, operation).model_dump(mode='json')
            link = _put(session, body.project_id, LINK, dict(schema_version=SCHEMAS[LINK], id=uid('layoutrun'), revision=1,
                project_id=body.project_id, layout_ref=_ref(layout), execution_id=execution['id'], input_hash=execution['input_hash'],
                created_at=utcnow(), author=dict(user_label=principal.user_label, role=principal.role)))
            _remember(session, body.project_id, operation, request_hash, LINK, link)
            return {**link, 'execution': execution}

    @api.get('/sensor-sim/layouts/{identity}/executions', tags=['sensor layouts'])
    def executions(identity: str, request: Request, project_id: str = 'default', revision: int | None = Query(None, ge=1),
                   limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            _get(session, project_id, LAYOUT, identity, revision)
            links = _links(session, project_id, identity, revision)
            result = _page(links, limit, offset, 'sensor-layout-executions/1')
            result['items'] = [_link_result(session, project_id, link) for link in result['items']]
            return result

    @api.post('/sensor-sim/layout-observations', status_code=201, tags=['sensor layouts'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def observe(body: SensorLayoutObservationCreate, request: Request,
                idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, request_hash, prior = _operation(session, body.project_id, 'observe', idempotency_key, body)
            if prior:
                return prior
            layout = _layout(session, body.project_id, body.layout_ref)
            if not any(link['execution_id'] == body.execution_id and link['layout_ref'] == _ref(layout)
                       for link in _links(session, body.project_id, layout['id'], layout['revision'])):
                raise HTTPException(409, 'Execution is not linked to this exact saved layout revision')
            execution, snapshot = _execution(session, body.project_id, body.execution_id, layout)
            samples = _samples(db, session, execution, snapshot, body.sensor_key, body.side, body.time_s)
            value = dict(schema_version=SCHEMAS[OBSERVATION], id=uid('layoutnote'), revision=1,
                **body.model_dump(mode='json'), created_at=utcnow(), author=dict(user_label=principal.user_label, role=principal.role),
                execution=_execution_metadata(execution), samples=samples, assessment_source='user_observation',
                time_basis='retained_frame_clock_user_selection', verification_status='not_a_test_verdict')
            result = _put(session, body.project_id, OBSERVATION, value)
            _remember(session, body.project_id, operation, request_hash, OBSERVATION, result)
            return result

    @api.get('/sensor-sim/layout-observations', tags=['sensor layouts'])
    def observations(request: Request, project_id: str = 'default', execution_id: str | None = None, layout_id: str | None = None,
                     revision: int | None = Query(None, ge=1), limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            if execution_id:
                _execution(session, project_id, execution_id)
            if layout_id:
                _get(session, project_id, LAYOUT, layout_id, revision)
            elif revision is not None:
                raise HTTPException(422, 'Observation revision filtering requires layout_id')
            return _page(_observations(session, project_id, execution_id, layout_id, revision), limit, offset, 'sensor-layout-observations/1')

    @api.get('/sensor-sim/layouts/{identity}/export', tags=['sensor layouts'])
    def export(identity: str, request: Request, project_id: str = 'default', revision: int | None = Query(None, ge=1)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            layout = _get(session, project_id, LAYOUT, identity, revision)
            links = [_link_result(session, project_id, link) for link in _links(session, project_id, identity, layout['revision'])]
            rows = [dict(layout_ref=link['layout_ref'], execution=_execution_metadata(link['execution']),
                artifacts=[{key: item[key] for key in ('id', 'name', 'sha256', 'size_bytes', 'media_type')}
                           for item in link['execution']['artifacts']]) for link in links]
            value = dict(schema_version='sensor-layout-export/1', layout=layout, executions=rows,
                observations=_observations(session, project_id, layout_id=identity, revision=layout['revision']),
                exported_at=utcnow(), limitations=LIMITATIONS, verification_status='not_a_test_verdict')
            raw = canonical_bytes(value)
            return Response(raw, media_type='application/json', headers={'X-Content-SHA256': hashlib.sha256(raw).hexdigest(),
                'Content-Disposition': f'attachment; filename="{identity}-r{layout["revision"]}.json"'})
