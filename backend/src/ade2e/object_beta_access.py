"""Opt-in invited Object Sim limits on the existing durable experiment queue.

One dedicated project is one invited participant. The SQLite writer transaction
arbitrates admission and worker claims across browsers, tokens, and processes.
"""
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from .experiment_models import ExperimentExecutionRow
from .job_models import JobRow
from .team_models import ObjectBetaPolicyRow
from .service import utcnow

ACTIVE_LIMIT = 1
QUEUE_LIMIT = 2


class ObjectBetaPolicyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    resource_id: str = Field(pattern=r'^carla-city-[0-9]{1,5}$')


def _counts(session, project_id):
    rows = session.execute(select(JobRow.status, func.count()).join(
        ExperimentExecutionRow, ExperimentExecutionRow.job_id == JobRow.id).where(
            ExperimentExecutionRow.project_id == project_id,
            JobRow.status.in_(['queued', 'running', 'cancel_requested'])).group_by(JobRow.status))
    counts = dict(rows.all())
    return counts.get('running', 0) + counts.get('cancel_requested', 0), counts.get('queued', 0)


def policy_status(session, project_id):
    policy = session.get(ObjectBetaPolicyRow, project_id)
    active, queued = _counts(session, project_id)
    enabled = bool(policy and policy.enabled)
    return {'schema_version': 'object-beta-policy/1', 'project_id': project_id, 'enabled': enabled,
            'active_limit': ACTIVE_LIMIT if enabled else None, 'queue_limit': QUEUE_LIMIT if enabled else None,
            'active_count': active, 'queued_count': queued,
            'remaining_queue_slots': max(0, QUEUE_LIMIT - queued) if enabled else None,
            'can_submit': not enabled or queued < QUEUE_LIMIT,
            'resource_id': policy.resource_id if policy else None,
            'identity_scope': 'dedicated_project', 'updated_at': policy.updated_at if policy else None}


def admit_experiment(session, project_id, spec, metadata):
    """Call after idempotency lookup and before insertion, under jobs._write."""
    policy = session.get(ObjectBetaPolicyRow, project_id)
    if policy is None or not policy.enabled:
        return
    if spec['kind'] != 'object_sim' or metadata.resource_id != policy.resource_id:
        raise HTTPException(422, '이 베타 프로젝트는 지정된 CARLA Object Sim 실행만 지원합니다. Object Sim 템플릿을 선택해 주세요.')
    _, queued = _counts(session, project_id)
    if queued >= QUEUE_LIMIT:
        raise HTTPException(429,
            f'실행 대기 한도({QUEUE_LIMIT}개)에 도달했습니다. 대기 작업을 취소하거나 실행이 시작된 뒤 다시 제출해 주세요. 실행 중 한도는 {ACTIVE_LIMIT}개입니다.',
            headers={'Retry-After': '10'})


def can_claim_experiment(session, row):
    """Use the existing scheduler; cancelled requests remain active until ack."""
    if row.kind != 'experiment':
        return True
    subject = session.get(ExperimentExecutionRow, row.id)
    if subject is None:
        return True  # The normal immutable-subject check reports corruption.
    policy = session.get(ObjectBetaPolicyRow, subject.project_id)
    return policy is None or not policy.enabled or _counts(session, subject.project_id)[0] < ACTIVE_LIMIT


def register_object_beta_access_routes(api, db):
    from . import jobs
    from .team_access import project_authorize

    @api.get('/projects/{project_id}/object-beta-policy', tags=['projects'])
    def get_policy(project_id: str, request: Request):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return policy_status(session, project_id)

    @api.post('/projects/{project_id}/object-beta-policy', tags=['projects'],
              openapi_extra={'x-axiom-min-role': 'Admin', 'x-axiom-operator-only': True})
    def set_policy(project_id: str, body: ObjectBetaPolicyInput, request: Request):
        project_authorize(request, db, project_id, 'Admin')
        if request.state.principal.mode != 'operator':
            raise HTTPException(403, 'Only the bootstrap operator may configure beta admission')
        with jobs._write(db) as session:
            row = session.get(ObjectBetaPolicyRow, project_id)
            if body.enabled and (row is None or not row.enabled or row.resource_id != body.resource_id):
                active, queued = _counts(session, project_id)
                if active or queued:
                    raise HTTPException(409, 'Wait for active jobs and cancel queued jobs before enabling or changing the beta resource')
            if row is None:
                row = ObjectBetaPolicyRow(project_id=project_id)
                session.add(row)
            row.enabled, row.resource_id, row.updated_at = body.enabled, body.resource_id, utcnow()
            session.flush()
            return policy_status(session, project_id)
