"""Project-scoped immutable observations over exact Object Sim executions.

Existing investigation Cases require recordings or evaluated images. These
execution observations retain that boundary rather than forging a recording.
"""
import hashlib
import json
import re
from typing import Literal
from fastapi import Header, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .base_schemas import StrictModel
from .object_demo_schemas import DemoScenarioReference
from .scenario_v2_schemas import Identity
from .scenario_v2_xml import canonical_bytes
from .service import utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

KIND = 'object-observation'
_CREDENTIAL = re.compile(r'(?i)(?:\bbearer\s+\S+|\baxiom_[A-Za-z0-9_-]{16,}|\b(?:api[_ -]?token|access[_ -]?token|password|authorization)\s*[:=]\s*\S+)')


class ObjectObservationCreate(StrictModel):
    project_id: Identity = 'default'
    execution_id: Identity
    scenario_ref: DemoScenarioReference
    time_s: float = Field(ge=0, le=120)
    title: str = Field(min_length=1, max_length=180)
    note: str = Field(min_length=1, max_length=4000)
    kind: Literal['observation', 'issue'] = 'observation'
    behavior_exercised: Literal['observed', 'not_observed', 'uncertain'] = 'uncertain'
    ego_outcome: Literal['arrived', 'stopped', 'collision', 'other', 'uncertain'] = 'uncertain'

    @field_validator('title', 'note')
    @classmethod
    def safe_note(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('An observation title and note are required')
        if _CREDENTIAL.search(value):
            raise ValueError('Remove credentials from the observation; only describe the execution')
        return value


def _read(row):
    try:
        if hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
            raise ValueError('hash')
        value = json.loads(row.manifest)
        if (value['id'], value['project_id'], value['revision']) != (row.id, row.project_id, row.revision):
            raise ValueError('identity')
        return {key: item for key, item in value.items() if key != 'request_hash'} | {'sha256': row.manifest_hash}
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, 'Object Sim observation integrity check failed') from exc


def register_object_observation_routes(api, db):
    @api.post('/object-sim/observations', status_code=201, tags=['Object Sim'],
              openapi_extra={'x-axiom-min-role': 'Engineer'})
    def create(body: ObjectObservationCreate, request: Request,
               idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        identity = 'observation_' + hashlib.sha256((body.project_id + '\0' + idempotency_key).encode()).hexdigest()[:48]
        request_hash = hashlib.sha256(canonical_bytes(body.model_dump(mode='json'))).hexdigest()
        from .object_demo import _execution
        try:
            with db.session() as session:
                existing = session.get(Row, (body.project_id, KIND, identity, 1))
                if existing:
                    result = _read(existing)
                    if json.loads(existing.manifest)['request_hash'] != request_hash:
                        raise HTTPException(409, 'Observation idempotency key was already used for different content')
                    return result
                source, _ = _execution(session, body.project_id, body.execution_id)
                if source['scenario_ref'] != body.scenario_ref.model_dump(mode='json'):
                    raise HTTPException(409, 'Observation scenario pin differs from the executed revision')
                execution = source['execution']
                if execution['status'] not in {'completed', 'failed', 'cancelled', 'interrupted'}:
                    raise HTTPException(422, 'Wait for a terminal execution before recording its observation')
                if body.time_s > source['scenario']['duration_s']:
                    raise HTTPException(422, 'Observation time exceeds the scenario clock interval')
                value = {'schema_version': 'object-sim-observation/1', 'id': identity, 'revision': 1,
                    **body.model_dump(mode='json'), 'created_at': utcnow(),
                    'author': {'user_label': principal.user_label, 'role': principal.role},
                    'execution': {key: execution.get(key) for key in ('id', 'status', 'input_hash', 'manifest_hash',
                        'adapter_id', 'adapter_version', 'fidelity', 'outcome_uncertain')},
                    'assessment_source': 'user_observation', 'time_basis': 'scenario_clock_user_selection',
                    'verification_status': 'not_a_test_verdict', 'request_hash': request_hash}
                raw = canonical_bytes(value)
                row = Row(project_id=body.project_id, kind=KIND, id=identity, revision=1,
                          manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest())
                session.add(row); session.flush(); result = _read(row); session.commit()
                return result
        except IntegrityError as exc:
            # A concurrent identical request can be read using the same key.
            raise HTTPException(409, 'Concurrent observation save; retry the same idempotency key') from exc

    @api.get('/object-sim/observations', tags=['Object Sim'])
    def observations(request: Request, project_id: str = 'default', execution_id: str | None = None,
                     limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        from .object_demo import _execution
        with db.read_session() as session:
            if execution_id:
                _execution(session, project_id, execution_id)
            # Catalog rows are bounded project metadata, never arbitrary files.
            query = select(Row).where(Row.project_id == project_id, Row.kind == KIND).order_by(Row.id)
            items = [_read(row) for row in session.scalars(query)]
            if execution_id:
                items = [item for item in items if item['execution_id'] == execution_id]
            items.sort(key=lambda item: (item['created_at'], item['id']), reverse=True)
            return {'schema_version': 'object-sim-observations/1', 'project_id': project_id,
                'items': items[offset:offset + limit], 'next_offset': offset + limit if len(items) > offset + limit else None,
                'assessment_source': 'user_observation', 'verification_status': 'not_a_test_verdict'}
