"""Guided internal beta work, pinned to actual sources, comparisons and reviews."""
import csv
import io
import json
from threading import RLock
from typing import Literal

from fastapi import Header, HTTPException, Query, Request, Response
from pydantic import Field
from sqlalchemy import select

from . import cases, log_sim, log_sim_batches as batches, log_sim_replays as replay
from .base_schemas import StrictModel
from .log_sim_beta import REVIEW, SETTINGS, SettingsRef, settings_profiles
from .log_sim_beta_packs import PACKS
from .service import digest, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Catalog
from .team_access import project_authorize
from .team_models import ProjectRow

TASK = 'log-beta-task'
FEEDBACK = 'log-beta-feedback'
WRITE = 'log-beta-task-write'
LOCK = RLock()
Stage = Literal['source', 'settings', 'execution', 'investigation', 'review', 'completed']


class TaskCreate(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    pack_id: str = Field(min_length=1, max_length=100)


class TaskProgress(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)
    source_id: str | None = Field(default=None, min_length=1, max_length=100)
    comparison_ref: SettingsRef | None = None
    batch_id: str | None = Field(default=None, min_length=1, max_length=100)
    case_id: str | None = Field(default=None, min_length=1, max_length=100)
    result_opened: bool | None = None


class FeedbackCreate(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=100)
    stage: Stage
    kind: Literal['blocked', 'completion']
    message: str = Field(min_length=1, max_length=4000)
    needs_help: bool | None = None


def _principal_id(principal):
    return digest(['log-beta-participant/1', principal.token_id or principal.mode])


def _source(db, request, session, project, identity, expected_hash=None):
    value = log_sim._source(session, project, identity)
    batches._source_access(request, db, project, value)
    if expected_hash and value['source_hash'] != expected_hash:
        raise HTTPException(409, 'Task source changed; keep this record and start a new task')
    return value


def _feedback(session, project, task_id):
    return [value for value in replay._values(session, project, FEEDBACK) if value['task_id'] == task_id]


def _check_batch(db, request, session, value):
    batch = replay._get(session, value['project_id'], batches.BATCH, value['batch_id'])
    batches._authorize_saved(db, request, batch)
    if (len(batch['sources']) != 1 or batch['sources'][0]['source_id'] != value['source_id']
            or batch['sources'][0]['source_hash'] != value['source_hash']):
        raise HTTPException(409, 'Task requires a batch of its exact single original source')
    if batch.get('comparison_ref') != value['comparison_ref']:
        raise HTTPException(409, 'Batch does not use the task comparison settings revision')
    return batch


def _check_case(session, value, batch):
    owned = log_sim._catalog(session, value['project_id'], log_sim.CASE_KIND, value['case_id'])
    if (not owned or owned['definition']['source_id'] != value['source_id']
            or owned['definition']['source_hash'] != value['source_hash']):
        raise HTTPException(404, 'Case is not linked to this task source')
    ref = owned['definition']
    # A manually selected original interval has no comparison ID. Candidate Cases
    # must originate from this batch, including a retained previous retry attempt.
    pair_ids = {row['comparison_id'] for row in batch['sources']}
    pair_ids.update(identity for row in batch['sources'] for identity in row.get('attempts', []))
    if ref.get('comparison_id') and ref['comparison_id'] not in pair_ids:
        raise HTTPException(409, 'Candidate Case belongs to a different comparison batch')
    actual = cases._get(session, ref['case_id'], ref['case_revision'])
    if actual['manifest_hash'] != ref['case_manifest_hash']:
        raise HTTPException(409, 'Case manifest integrity check failed')
    return ref


def _present(db, request, session, value):
    result = dict(value, status='active', stage='source', execution_status=None,
                  execution_reason=None, review_status=None, needs_help=None,
                  completion_feedback=False, assistance_self_reported=None)
    if value['source_id']:
        _source(db, request, session, value['project_id'], value['source_id'], value['source_hash'])
        result['stage'] = 'settings'
    if value['comparison_ref']:
        settings_profiles(session, value['project_id'], SettingsRef(**value['comparison_ref']))
        result['stage'] = 'execution'
    if value['batch_id']:
        batch = _check_batch(db, request, session, value)
        report = batches.report_batch(db, session, batch)
        result['execution_status'] = report['status']
        result['execution_reason'] = next((row.get('reason') for row in report['sources'] if row.get('reason')), None)
        completed = report['status'] == 'completed' and all(row['status'] == 'completed' for row in report['sources'])
        if report['status'] not in {'queued', 'running', 'completed'}:
            result['status'] = 'blocked'
        if completed:
            result['stage'] = 'review' if value['result_opened'] else 'investigation'
        if value['case_id']:
            case_ref = _check_case(session, value, batch)
            review = log_sim._catalog(session, value['project_id'], REVIEW, value['case_id'])
            if review:
                detail = review['definition']
                if (detail['source_hash'] != value['source_hash'] or detail['case_ref'] != case_ref):
                    raise HTTPException(409, 'Saved Case review does not match the task Case revision')
                result['review_status'] = detail['status']
                if completed and value['result_opened'] and detail['status'] == 'reviewed':
                    result.update(status='completed', stage='completed')
    feedback = _feedback(session, value['project_id'], value['id'])
    if feedback:
        latest = feedback[0]
        result['needs_help'] = latest['needs_help']
        completed_feedback = next((item for item in feedback if item['kind'] == 'completion'), None)
        if completed_feedback:
            result.update(completion_feedback=True, assistance_self_reported=completed_feedback['needs_help'])
        if (latest['kind'] == 'blocked' and latest['created_at'] >= value['updated_at']
                and result['status'] != 'completed'):
            result['status'] = 'blocked'
    return result


def _operation(session, project, operation, payload):
    previous = log_sim._catalog(session, project, WRITE, operation)
    if previous:
        if previous['definition']['request'] != payload:
            raise HTTPException(409, 'Idempotency key belongs to another beta task write')
        return previous['definition']['result']
    return None


def _save_operation(session, project, operation, payload, result):
    replay._put(session, project, WRITE, operation, {'request': payload, 'result': result})


def _csv_cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def register_log_sim_beta_task_routes(api, db):
    @api.get('/log-sim/comparison-settings/{identity}', tags=['Log Sim beta'])
    def settings_revision(identity: str, request: Request, project_id: str = 'default', revision: int = Query(ge=1)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            row = session.get(Catalog, (project_id, SETTINGS, identity, revision))
            if row is None:
                raise HTTPException(404, 'Comparison settings revision not found')
            manifest = json.loads(row.manifest)
            if digest(manifest) != row.manifest_hash:
                raise HTTPException(409, 'Comparison settings integrity check failed')
            value = manifest['definition']
            settings_profiles(session, project_id, SettingsRef(id=identity, revision=revision, sha256=value['sha256']))
            return value

    @api.post('/log-sim/beta-tasks', status_code=201, tags=['Log Sim beta'])
    def create(body: TaskCreate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        if body.pack_id not in {pack['id'] for pack in PACKS}:
            raise HTTPException(404, 'Beta pack not found')
        identity = 'logtask_' + digest([body.project_id, idempotency_key])[:32]
        operation = 'create_' + digest([body.project_id, idempotency_key])[:32]
        payload = body.model_dump()
        with LOCK, db.session.begin() as session:
            previous = _operation(session, body.project_id, operation, payload)
            if previous is not None:
                return previous
            source_id = source_hash = None
            if body.pack_id != 'input-errors':
                binding = log_sim._catalog(session, body.project_id, 'log-beta-pack', body.pack_id)
                if not binding:
                    raise HTTPException(409, 'Pack source is not connected; ask the operator to prepare this pack')
                bound = binding['definition']
                source = _source(db, request, session, body.project_id, bound['source_id'], bound['source_hash'])
                source_id, source_hash = source['id'], source['source_hash']
            now = utcnow()
            value = dict(schema_version='log-beta-task/1', id=identity, project_id=body.project_id,
                pack_id=body.pack_id, revision=1, source_id=source_id, source_hash=source_hash,
                comparison_ref=None, batch_id=None, case_id=None, result_opened=False,
                created_at=now, updated_at=now, participant=principal.user_label, participant_id=_principal_id(principal))
            replay._put(session, body.project_id, TASK, identity, value)
            result = _present(db, request, session, value)
            _save_operation(session, body.project_id, operation, payload, result)
            return result

    @api.get('/log-sim/beta-tasks', tags=['Log Sim beta'])
    def listing(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return {'items': [_present(db, request, session, value) for value in replay._values(session, project_id, TASK)]}

    @api.get('/log-sim/beta-tasks/{identity}', tags=['Log Sim beta'])
    def read(identity: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return _present(db, request, session, replay._get(session, project_id, TASK, identity))

    @api.post('/log-sim/beta-tasks/{identity}/progress', tags=['Log Sim beta'])
    def progress(identity: str, body: TaskProgress, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        operation = 'progress_' + digest([body.project_id, identity, idempotency_key])[:32]
        payload = body.model_dump() | {'task_id': identity}
        with LOCK, db.session.begin() as session:
            previous = _operation(session, body.project_id, operation, payload)
            if previous is not None:
                return previous
            stored = replay._get(session, body.project_id, TASK, identity)
            if body.expected_revision != stored['revision']:
                raise HTTPException(409, 'Task revision changed; reload before saving')
            value = dict(stored)
            if (principal.mode == 'project' and stored['participant_id'] in {
                    digest(['log-beta-participant/1', 'operator']), digest(['log-beta-participant/1', 'legacy'])}):
                value['created_by'] = {key: stored[key] for key in ('participant', 'participant_id')}
                value.update(participant=principal.user_label, participant_id=_principal_id(principal))
            if body.source_id:
                if stored['source_id'] and body.source_id != stored['source_id']:
                    raise HTTPException(409, 'Task original source is pinned; start another task to change it')
                source = _source(db, request, session, body.project_id, body.source_id, stored['source_hash'])
                if stored['pack_id'] == 'input-errors' and source['kind'] != 'recorded':
                    raise HTTPException(422, 'The input exercise requires a registered uploaded source')
                value.update(source_id=source['id'], source_hash=source['source_hash'])
            if body.comparison_ref:
                ref = body.comparison_ref.model_dump()
                if stored['batch_id'] and stored['comparison_ref'] != ref:
                    raise HTTPException(409, 'Executed comparison settings are pinned; start another task')
                if not value['source_id']:
                    raise HTTPException(422, 'Register the task source before saving comparison settings')
                settings_profiles(session, body.project_id, body.comparison_ref)
                value['comparison_ref'] = ref
            if body.batch_id:
                if stored['batch_id'] and stored['batch_id'] != body.batch_id:
                    raise HTTPException(409, 'Task execution is pinned; start another task for a new batch')
                if not value['comparison_ref']:
                    raise HTTPException(422, 'Save the task comparison settings before linking execution')
                value['batch_id'] = body.batch_id
            if body.result_opened is not None:
                if body.result_opened and not value['batch_id']:
                    raise HTTPException(422, 'Run the task comparison before opening its results')
                if stored['result_opened'] and not body.result_opened:
                    raise HTTPException(409, 'An investigated result cannot be marked unopened')
                value['result_opened'] = body.result_opened
            if body.case_id:
                if not value['batch_id']:
                    raise HTTPException(422, 'Run the task comparison before linking its Case')
                if stored['case_id'] and body.case_id != stored['case_id']:
                    raise HTTPException(409, 'Task Case is pinned; retain this investigation in a new task')
                value['case_id'] = body.case_id
            value.update(revision=stored['revision'] + 1, updated_at=utcnow())
            result = _present(db, request, session, value)
            if body.result_opened and result['execution_status'] != 'completed':
                raise HTTPException(409, 'Wait for this comparison to complete before recording investigation')
            replay._put(session, body.project_id, TASK, identity, value)
            _save_operation(session, body.project_id, operation, payload, result)
            return result

    @api.post('/log-sim/beta-feedback', status_code=201, tags=['Log Sim beta'])
    def feedback_create(body: FeedbackCreate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        if not body.message.strip():
            raise HTTPException(422, 'Describe what happened before sending feedback')
        identity = 'logfeedback_' + digest([body.project_id, idempotency_key])[:32]
        operation = 'feedback_' + digest([body.project_id, idempotency_key])[:32]
        payload = body.model_dump()
        with LOCK, db.session.begin() as session:
            previous = _operation(session, body.project_id, operation, payload)
            if previous is not None:
                return previous
            task = _present(db, request, session, replay._get(session, body.project_id, TASK, body.task_id))
            if body.kind == 'completion' and task['status'] != 'completed':
                raise HTTPException(409, 'Complete comparison investigation and save a reviewed Case first')
            value = payload | dict(id=identity, schema_version='log-beta-feedback/1', created_at=utcnow(),
                participant=principal.user_label, participant_id=_principal_id(principal), task_revision=task['revision'],
                task_stage=task['stage'], source_id=task['source_id'], source_hash=task['source_hash'],
                comparison_ref=task['comparison_ref'], batch_id=task['batch_id'], case_id=task['case_id'])
            replay._put(session, body.project_id, FEEDBACK, identity, value)
            _save_operation(session, body.project_id, operation, payload, value)
            return value

    @api.get('/log-sim/beta-feedback', tags=['Log Sim beta'])
    def feedback_list(request: Request, project_id: str = 'default', task_id: str | None = None):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            if task_id:
                replay._get(session, project_id, TASK, task_id)
            values = replay._values(session, project_id, FEEDBACK)
            return {'items': [value for value in values if task_id is None or value['task_id'] == task_id]}

    @api.get('/log-sim/beta-overview', tags=['Log Sim beta'], openapi_extra={'x-axiom-operator-only': True})
    def overview(request: Request, format: Literal['json', 'csv'] = 'json'):
        principal = getattr(request.state, 'principal', None)
        if principal is None:
            raise HTTPException(401, 'Authenticated operator context is required')
        if principal.mode == 'project':
            raise HTTPException(403, 'Only the operator can view beta participants across projects')
        with db.read_session() as session:
            projects = [{'id': row.id, 'name': row.name, 'created_at': row.created_at}
                        for row in session.scalars(select(ProjectRow).order_by(ProjectRow.created_at, ProjectRow.id))]
            tasks, feedback = [], []
            for project in projects:
                for value in replay._values(session, project['id'], TASK):
                    try:
                        tasks.append(_present(db, request, session, value))
                    except HTTPException as error:
                        tasks.append(value | dict(status='blocked', stage='source', execution_status=None,
                            execution_reason=str(error.detail), review_status=None, needs_help=True))
                feedback.extend(replay._values(session, project['id'], FEEDBACK))
            counts = dict(started=len(tasks), active=sum(t['status'] == 'active' for t in tasks),
                completed=sum(t['status'] == 'completed' for t in tasks), blocked=sum(t['status'] == 'blocked' for t in tasks),
                participants=len({t['participant_id'] for t in tasks}), feedback=len(feedback),
                completed_without_help=sum(t['status'] == 'completed' and t.get('completion_feedback')
                    and t.get('assistance_self_reported') is False for t in tasks))
            if format == 'json':
                return dict(schema_version='log-beta-overview/1', projects=projects, tasks=tasks, feedback=feedback, counts=counts)
            out = io.StringIO()
            columns = ['project_id', 'participant', 'id', 'pack_id', 'status', 'stage', 'execution_status',
                       'source_id', 'source_hash', 'batch_id', 'case_id', 'review_status', 'needs_help', 'created_at', 'updated_at', 'feedback_count', 'latest_feedback']
            writer = csv.writer(out); writer.writerow(columns)
            for task in tasks:
                comments = sorted((f for f in feedback if f['task_id'] == task['id'] and f['project_id'] == task['project_id']), key=lambda f: f['created_at'], reverse=True)
                row = task | dict(feedback_count=len(comments), latest_feedback=comments[0]['message'] if comments else '')
                writer.writerow([_csv_cell(row.get(column)) for column in columns])
            return Response(out.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="log-beta-overview.csv"'})
