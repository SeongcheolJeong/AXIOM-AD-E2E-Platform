"""Versioned configuration authoring with endpoint-scoped atomic idempotency."""
import hashlib

from fastapi import Header, HTTPException, Query, Response
from sqlalchemy import func, select

from . import jobs
from .service import uid, utcnow
from .simulation_sensor_capabilities import (canonical, content_hash, simulation_sensor_capabilities,
    validate_sensor_configuration_snapshot)
from .simulation_sensor_models import (SensorConfigurationRow, SensorConfigurationRevisionRow,
    SensorConfigurationIdempotencyRow)
from .simulation_sensor_schemas import (SensorConfigurationCreate, SensorConfigurationRevisionCreate,
    SensorConfigurationSnapshot, SensorConfigurationDetail, SensorExecutionCapability)


def get_sensor_configuration(session, configuration_id, revision=None):
    head = session.get(SensorConfigurationRow, configuration_id)
    if head is None:
        raise HTTPException(404, 'Sensor configuration not found')
    latest = session.scalar(select(func.max(SensorConfigurationRevisionRow.revision)).where(
        SensorConfigurationRevisionRow.configuration_id == configuration_id))
    if head.revision != latest or type(head.revision) is not int or head.revision < 1:
        raise HTTPException(409, 'Sensor configuration head differs from its revision history')
    row = session.get(SensorConfigurationRevisionRow, (configuration_id, head.revision if revision is None else revision))
    if row is None:
        raise HTTPException(404, 'Sensor configuration revision not found')
    try:
        snapshot = SensorConfigurationSnapshot.model_validate_json(row.manifest)
        detail = SensorConfigurationDetail(**snapshot.model_dump(mode='json'), manifest_hash=row.manifest_hash,
            manifest_size_bytes=row.manifest_size_bytes,
            download_url=f'/api/v1/simulation-sensor-configurations/{configuration_id}/export?revision={row.revision}')
        validate_sensor_configuration_snapshot(detail)
        if (canonical(snapshot.model_dump(mode='json')) != row.manifest
                or snapshot.id != row.configuration_id or snapshot.revision != row.revision
                or snapshot.created_at != row.created_at):
            raise ValueError('Stored configuration metadata differs from its manifest')
        return detail
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(409, 'Sensor configuration failed its integrity check') from error


def save_sensor_configuration(db, body, key, configuration_id=None):
    if not isinstance(key, str) or not key.strip() or len(key)>200:
        raise HTTPException(422, 'A nonempty Idempotency-Key of at most 200 characters is required')
    scope = 'create' if configuration_id is None else f'revise:{configuration_id}'
    request_hash = content_hash(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        previous = session.get(SensorConfigurationIdempotencyRow, (scope, key))
        if previous:
            if previous.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key was used for a different sensor configuration request')
            return get_sensor_configuration(session, previous.configuration_id, previous.revision), False
        if configuration_id is None:
            identifier, revision, now = uid('sensorcfg'), 1, utcnow()
            session.add(SensorConfigurationRow(id=identifier, revision=revision, created_at=now))
            session.flush()
        else:
            current = get_sensor_configuration(session, configuration_id)
            if current.revision != body.expected_revision:
                raise HTTPException(409, 'Sensor configuration revision changed; reload before revising')
            if current.revision >= 2**31-1:
                raise HTTPException(409, 'Sensor configuration revision limit reached')
            identifier, revision, now = configuration_id, current.revision+1, utcnow()
            session.get(SensorConfigurationRow, configuration_id).revision = revision
        definition = SensorConfigurationCreate.model_validate(body.model_dump(mode='json', exclude={'expected_revision'}))
        snapshot = SensorConfigurationSnapshot(**definition.model_dump(mode='json'), id=identifier,
            revision=revision, created_at=now, definition_hash=content_hash(definition.model_dump(mode='json')),
            capability_schema_hash=simulation_sensor_capabilities()[0].schema_hash)
        content = canonical(snapshot.model_dump(mode='json'))
        if len(content)>65536:
            raise HTTPException(413, 'Sensor configuration manifest exceeds 64 KiB')
        session.add(SensorConfigurationRevisionRow(configuration_id=identifier, revision=revision,
            created_at=now, manifest=content, manifest_hash=hashlib.sha256(content).hexdigest(), manifest_size_bytes=len(content)))
        session.flush()
        session.add(SensorConfigurationIdempotencyRow(scope=scope, key=key, request_hash=request_hash,
            configuration_id=identifier, revision=revision))
        session.flush()
        return get_sensor_configuration(session, identifier, revision), True


def register_simulation_sensor_routes(api, db):
    @api.get('/simulation-sensor-capabilities', response_model=list[SensorExecutionCapability], tags=['simulation sensors'])
    def capabilities(adapter_id: str | None = Query(None)):
        return [item for item in simulation_sensor_capabilities() if adapter_id is None or item.adapter_id == adapter_id]

    @api.post('/simulation-sensor-configurations', response_model=SensorConfigurationDetail, status_code=201, tags=['simulation sensors'])
    def create(body: SensorConfigurationCreate, response: Response, idempotency_key: str = Header(alias='Idempotency-Key')):
        detail, created = save_sensor_configuration(db, body, idempotency_key)
        response.status_code = 201 if created else 200
        return detail

    @api.get('/simulation-sensor-configurations', response_model=list[SensorConfigurationDetail], tags=['simulation sensors'])
    def heads():
        with db.read_session() as session:
            return [get_sensor_configuration(session, identifier) for identifier in session.scalars(
                select(SensorConfigurationRow.id).order_by(SensorConfigurationRow.created_at.desc(), SensorConfigurationRow.id.desc()))]

    @api.get('/simulation-sensor-configurations/{configuration_id}', response_model=SensorConfigurationDetail, tags=['simulation sensors'])
    def get(configuration_id: str, revision: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            return get_sensor_configuration(session, configuration_id, revision)

    @api.post('/simulation-sensor-configurations/{configuration_id}/revisions', response_model=SensorConfigurationDetail, status_code=201, tags=['simulation sensors'])
    def revise(configuration_id: str, body: SensorConfigurationRevisionCreate, response: Response,
               idempotency_key: str = Header(alias='Idempotency-Key')):
        detail, created = save_sensor_configuration(db, body, idempotency_key, configuration_id)
        response.status_code = 201 if created else 200
        return detail

    @api.get('/simulation-sensor-configurations/{configuration_id}/revisions', response_model=list[SensorConfigurationDetail], tags=['simulation sensors'])
    def history(configuration_id: str):
        with db.read_session() as session:
            get_sensor_configuration(session, configuration_id)
            return [get_sensor_configuration(session, configuration_id, revision) for revision in session.scalars(
                select(SensorConfigurationRevisionRow.revision).where(SensorConfigurationRevisionRow.configuration_id == configuration_id)
                .order_by(SensorConfigurationRevisionRow.revision.desc()))]

    @api.get('/simulation-sensor-configurations/{configuration_id}/export', tags=['simulation sensors'])
    def export(configuration_id: str, revision: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            detail = get_sensor_configuration(session, configuration_id, revision)
            content = session.get(SensorConfigurationRevisionRow, (configuration_id, detail.revision)).manifest
            return Response(content, media_type='application/json', headers={
                'Content-Disposition': f'attachment; filename="{configuration_id}-r{detail.revision}.sensor-configuration.json"'})
