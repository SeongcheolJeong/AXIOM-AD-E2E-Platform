"""Immutable stack declarations; component registry controls supported execution."""
import hashlib
import copy
import json

from fastapi import HTTPException, Query, Response
from sqlalchemy import func, select

from . import jobs
from .service import canonical_bytes, uid, utcnow
from .stack_models import StackRow, StackRevisionRow
from .stack_schemas import (StackCreate, StackRevisionCreate, StackSnapshot, StackDetail,
                            StackComponentMetadata, validate_stack_definition, stack_kernel_version)

MAX_MANIFEST_BYTES = 256 * 1024
MANIFEST_FIELDS = {'manifest_hash', 'manifest_size_bytes', 'download_url'}


def validate_stack_snapshot(value):
    detail = StackDetail.model_validate(value)
    snapshot = StackSnapshot.model_validate(detail.model_dump(mode='json', exclude=MANIFEST_FIELDS))
    content = canonical_bytes(snapshot.model_dump(mode='json'))
    if (not 0 < len(content) <= MAX_MANIFEST_BYTES or len(content) != detail.manifest_size_bytes
            or hashlib.sha256(content).hexdigest() != detail.manifest_hash
            or detail.download_url != f'/api/v1/stacks/{detail.id}/export?revision={detail.revision}'):
        raise ValueError('Stack snapshot differs from its canonical definition')
    return detail


def get_stack(session, stack_id, revision=None):
    head = session.get(StackRow, stack_id)
    if head is None:
        raise HTTPException(404, 'Stack not found')
    latest = session.scalar(select(func.max(StackRevisionRow.revision)).where(StackRevisionRow.stack_id == stack_id))
    if head.revision != latest or not isinstance(head.revision, int) or head.revision < 1:
        raise HTTPException(409, 'Stack authoring head differs from its revision history')
    row = session.get(StackRevisionRow, (stack_id, head.revision if revision is None else revision))
    if row is None:
        raise HTTPException(404, 'Stack revision not found')
    try:
        snapshot = StackSnapshot.model_validate_json(row.manifest)
        detail = StackDetail(**snapshot.model_dump(mode='json'), manifest_hash=row.manifest_hash,
            manifest_size_bytes=row.manifest_size_bytes, download_url=f'/api/v1/stacks/{stack_id}/export?revision={row.revision}')
        validate_stack_snapshot(detail)
        if (canonical_bytes(snapshot.model_dump(mode='json')) != row.manifest or snapshot.id != row.stack_id
                or snapshot.revision != row.revision or snapshot.created_at != row.created_at):
            raise ValueError('Stored stack metadata differs from its manifest')
        return detail
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(409, 'Stack definition failed its integrity check') from error


def _values(snapshot):
    content = canonical_bytes(snapshot.model_dump(mode='json'))
    if len(content) > MAX_MANIFEST_BYTES:
        raise HTTPException(413, 'Stack definition exceeds 256 KiB')
    return {'manifest': content, 'manifest_hash': hashlib.sha256(content).hexdigest(), 'manifest_size_bytes': len(content)}


def _metadata(registry, definition):
    try:
        return registry.validate_definition(definition)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error)) from error


def _normalized_authoring(registry, body):
    """Freeze actual validator output, never just advertised metadata defaults.

    This runs only for a new revision. Historical reads/export must continue to
    return their original bytes, including revisions from a previous implementation.
    """
    definition = validate_stack_definition(body.definition)
    metadata = [item.model_copy(deep=True) for item in _metadata(registry, definition)]
    try:
        for role in ('perception', 'policy', 'controller'):
            selection = getattr(definition, role)
            component = registry.get(selection.id, selection.version)
            normalized = component.validate_parameters(copy.deepcopy(selection.parameters))
            if not isinstance(normalized, dict):
                raise ValueError('A component parameter validator must return a JSON object')
            # Reject non-JSON or nonfinite implementation output, and store the
            # same JSON representation that will be passed back during execution.
            content = canonical_bytes(normalized)
            selection.parameters = json.loads(content)
            repeated = component.validate_parameters(copy.deepcopy(selection.parameters))
            if not isinstance(repeated, dict) or canonical_bytes(repeated) != content:
                raise ValueError('Component parameter normalization must be stable for its complete saved parameters')
        current = _metadata(registry, definition)
        if current != metadata:
            raise ValueError('Component metadata changed during stack parameter normalization')
        # Recheck validator output after role/status validation, which can itself
        # invoke custom plugin code. A drifting normalization cannot be published.
        for role in ('perception', 'policy', 'controller'):
            selection = getattr(definition, role)
            component = registry.get(selection.id, selection.version)
            repeated = component.validate_parameters(copy.deepcopy(selection.parameters))
            if not isinstance(repeated, dict) or canonical_bytes(repeated) != canonical_bytes(selection.parameters):
                raise ValueError('Component parameter normalization changed during authoring')
        if [StackComponentMetadata.model_validate(registry.get(item.id, item.version).metadata()).model_copy(deep=True)
                for item in metadata] != metadata:
            raise ValueError('Component metadata changed during stack parameter normalization')
        return StackCreate(**(body.model_dump(mode='json', exclude={'expected_revision'}) |
                              {'definition': definition.model_dump(mode='json')})), metadata
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error)) from error


def create_stack(db, registry, body):
    body, metadata = _normalized_authoring(registry, body)
    with jobs._write(db) as session:
        identifier, now = uid('stack'), utcnow()
        snapshot = StackSnapshot(**body.model_dump(mode='json'), id=identifier, revision=1, created_at=now,
            kernel_version=stack_kernel_version(body.definition), component_metadata=metadata)
        session.add(StackRow(id=identifier, revision=1, created_at=now))
        session.flush()
        session.add(StackRevisionRow(stack_id=identifier, revision=1, created_at=now, **_values(snapshot)))
        session.flush()
        return get_stack(session, identifier, 1)


def revise_stack(db, registry, stack_id, body):
    definition, metadata = _normalized_authoring(registry, body)
    with jobs._write(db) as session:
        current = get_stack(session, stack_id)
        if current.revision != body.expected_revision:
            raise HTTPException(409, 'Stack revision changed; reload before revising')
        if current.revision >= 2**31-1:
            raise HTTPException(409, 'Stack revision limit reached')
        snapshot = StackSnapshot(**definition.model_dump(mode='json'), id=stack_id, revision=current.revision+1,
            created_at=utcnow(), kernel_version=stack_kernel_version(definition.definition), component_metadata=metadata)
        session.get(StackRow, stack_id).revision = snapshot.revision
        session.add(StackRevisionRow(stack_id=stack_id, revision=snapshot.revision, created_at=snapshot.created_at, **_values(snapshot)))
        session.flush()
        return get_stack(session, stack_id, snapshot.revision)


def register_stack_routes(api, db, registry):
    @api.get('/stack-components', response_model=list[StackComponentMetadata], tags=['stacks'])
    def components() -> list[StackComponentMetadata]:
        return registry.list()

    @api.post('/stacks', response_model=StackDetail, status_code=201, tags=['stacks'],
        description='Pin normalized parameters and exact component releases. Legacy definitions retain kernel 1; schema 2 and kernel 2 must be explicitly selected together. Kernel 2 plugins must preserve its versioned ground-grid, stopping-envelope and control recipe parameter contracts; this is not an arbitrary sensor interface.')
    def create(body: StackCreate):
        return create_stack(db, registry, body)

    @api.get('/stacks', response_model=list[StackDetail], tags=['stacks'])
    def heads():
        with db.read_session() as session:
            return [get_stack(session, identifier) for identifier in session.scalars(select(StackRow.id).order_by(StackRow.created_at.desc(), StackRow.id.desc()))]

    @api.get('/stacks/{stack_id}', response_model=StackDetail, tags=['stacks'])
    def get(stack_id: str, revision: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            return get_stack(session, stack_id, revision)

    @api.post('/stacks/{stack_id}/revisions', response_model=StackDetail, status_code=201, tags=['stacks'])
    def revise(stack_id: str, body: StackRevisionCreate):
        return revise_stack(db, registry, stack_id, body)

    @api.get('/stacks/{stack_id}/revisions', response_model=list[StackDetail], tags=['stacks'])
    def history(stack_id: str):
        with db.read_session() as session:
            get_stack(session, stack_id)
            return [get_stack(session, stack_id, revision) for revision in session.scalars(select(StackRevisionRow.revision).where(StackRevisionRow.stack_id == stack_id).order_by(StackRevisionRow.revision.desc()))]

    @api.get('/stacks/{stack_id}/export', tags=['stacks'])
    def export(stack_id: str, revision: int | None = Query(None, ge=1)):
        with db.read_session() as session:
            detail = get_stack(session, stack_id, revision)
            return Response(session.get(StackRevisionRow, (stack_id, detail.revision)).manifest, media_type='application/json',
                headers={'Content-Disposition': f'attachment; filename="{stack_id}-r{detail.revision}.stack.json"'})
