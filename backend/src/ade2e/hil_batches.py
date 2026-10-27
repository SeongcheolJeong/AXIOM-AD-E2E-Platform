"""Durable, sequential HIL batches on the normal Experiment queue.

Authoring is offline. Preview/start prepare outside SQLite write transactions.
The worker then commits one child job, normal test link and attempt membership
in one transaction. There is no browser dispatcher or ambiguous submit intent.
"""
import copy
import hashlib
import json
import logging
import math
from fastapi import Header, HTTPException, Query, Request, Response
from sqlalchemy import select
from . import experiment_jobs, hil_beta as beta, hil_demo, jobs
from .experiment_models import ExperimentArtifactRow
from .experiment_runtime import ExperimentHandlerMetadata
from .experiment_schemas import ExperimentCreate
from .hil_batches_schemas import HilBatchAction, HilBatchCreate, HilBatchSubmit
from .job_models import EngineResourceRow, JobRow
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

BATCH = 'hil-batch'
INPUT = 'hil-batch-input'
ACTIVE = {'queued', 'running', 'cancel_requested'}
RUNNING = {'running', 'stopping'}


def _definition(value):
    return {key: value[key] for key in ('id', 'project_id', 'name', 'note', 'created_at', 'author')} | {
        'tests': [item['test'] for item in value['items']]}


def _batch(session, project, identity, revision=None):
    value = beta._get(session, project, BATCH, identity, revision)
    if value['definition_sha256'] != digest(_definition(value)):
        raise HTTPException(409, 'Batch definition failed its integrity check')
    for index, item in enumerate(value['items']):
        if item['index'] != index or item['test_ref'] != beta._ref(item['test']):
            raise HTTPException(409, 'Batch item identity differs from its frozen test')
        if canonical_bytes(beta._test(session, project, item['test_ref'])) != canonical_bytes(item['test']):
            raise HTTPException(409, 'Batch test differs from its saved exact revision')
    return value


def _save(session, value):
    value = copy.deepcopy(value)
    value.pop('sha256', None)
    value['revision'] += 1
    value['updated_at'] = utcnow()
    return beta._put(session, value['project_id'], BATCH, value)


def _unfinished(value):
    return [item for item in value['items'] if item['status'] != 'completed']


def _idle(session, value):
    for resource_id in {item['resource_id'] for item in value['items'] if item.get('resource_id')}:
        row = session.get(EngineResourceRow, resource_id)
        if row is not None and row.state != 'idle':
            return False
    return True


def _resume_reason(session, value):
    if value['status'] not in {'stopped', 'cancelled'}:
        return 'Only a stopped or cancelled batch can resume'
    if value.get('resume_blocked'):
        return 'Resolve the recorded evidence integrity issue before creating a new batch'
    if not _unfinished(value):
        return 'Every item is already complete'
    for item in value['items']:
        if not item['attempts']:
            continue
        attempt = item['attempts'][-1]
        if attempt['execution_id']:
            row = jobs._row(session, attempt['execution_id'])
            if row.status not in beta.TERMINAL:
                return 'The previous execution is still active; wait for its terminal receipt'
            if row.outcome_uncertain:
                return 'The previous execution outcome is uncertain; it cannot be retried by batch resume'
    if not _idle(session, value):
        return 'The pinned execution resource must be idle before resume'
    return None


def _public(session, value, *, live=True):
    reason, executions = None, {}
    if value['status'] in {'stopped', 'cancelled'}:
        if live:
            reason = _resume_reason(session, value)
        elif value.get('resume_blocked'):
            reason = 'Resolve the recorded evidence integrity issue before creating a new batch'
        elif not _unfinished(value):
            reason = 'Every item is already complete'
        elif any(attempt['status'] not in beta.TERMINAL or attempt['outcome_uncertain']
                 for item in value['items'] for attempt in item['attempts'][-1:]):
            reason = 'The previous execution must be terminal with a certain outcome before resume'
    if live:
        # Read current queue state separately from the immutable batch snapshot.
        # No receipt reconciliation, revision update or dispatch is done by GET.
        for item in value['items']:
            for attempt in item['attempts']:
                if attempt['execution_id']:
                    row = jobs._row(session, attempt['execution_id'])
                    executions[row.id] = {key: getattr(row, key) for key in
                        ('id', 'status', 'outcome_uncertain', 'started_at', 'finished_at', 'cancel_requested_at')}
    return {**value, 'can_start': value['status'] == 'draft', 'can_cancel': value['status'] in {'draft', 'running', 'stopped'},
            'can_resume': value['status'] in {'stopped', 'cancelled'} and reason is None,
            'action_reason': reason or value.get('error'), 'live_executions': executions,
            'summary': {'total': len(value['items']), 'completed': sum(item['status'] == 'completed' for item in value['items']),
                        'attempts': sum(len(item['attempts']) for item in value['items'])}}


def _expected(value, revision):
    if value['revision'] != revision:
        raise HTTPException(409, 'Batch state changed; reload the current revision before this action')


def _prepare(db, registry, value):
    """No writer lock or execution dispatch; completed items are never re-prepared."""
    prepared, reviewed = {}, []
    for item in _unfinished(value):
        test = item['test']
        with db.read_session() as session:
            beta._test(session, value['project_id'], item['test_ref'])
            hil_demo._bound_source(session, value['project_id'], test['source'])
        frozen = experiment_jobs.prepare(registry, ExperimentCreate.model_validate(test['experiment']))
        payload = frozen.to_payload()
        hil_demo._snapshot_source(payload['input'], test['source'])
        hil_demo._snapshot_scene(payload['input'], test.get('scene_profile'))
        if 'dut_profiles' in test and canonical_bytes(payload['input'].get('dut_profiles')) != canonical_bytes(test['dut_profiles']):
            raise HTTPException(409, 'Prepared DUT profiles differ from the saved batch test')
        if item.get('input_hash') and item['input_hash'] != frozen.input_hash:
            raise HTTPException(409, 'Unfinished batch input changed since acceptance; create a new batch')
        prepared[item['index']] = frozen
        reviewed.append(dict(index=item['index'], test_ref=item['test_ref'], preview_hash=frozen.input_hash,
            execution_fidelity=payload['input'].get('execution_fidelity', payload['handler']['fidelity']),
            resource_id=payload['handler']['resource_id']))
    review = dict(schema_version='hil-sim-batch-preview/1', batch_ref=beta._ref(value),
                  definition_sha256=value['definition_sha256'], items=reviewed, job_count=len(reviewed),
                  requires_explicit_submit=True)
    return {**review, 'preview_hash': digest(review)}, prepared


def _input(session, value, item):
    frozen = beta._get(session, value['project_id'], INPUT, value['id'] + '_' + str(item['index']), 1)
    prepared = experiment_jobs.PreparedExperiment.from_payload(frozen['prepared'])
    if (frozen['test_ref'] != item['test_ref'] or prepared.input_hash != item['input_hash']
            or frozen['definition_sha256'] != value['definition_sha256']):
        raise HTTPException(409, 'Frozen batch input failed its identity check')
    return prepared


def _trace_summary(db, session, execution):
    """Retained response measurements only, bound to the verified terminal receipt."""
    result = {}
    for side in ('baseline', 'faulted'):
        name = side + '-hil-trace.json'
        rows = list(session.scalars(select(ExperimentArtifactRow).where(
            ExperimentArtifactRow.job_id == execution['id'], ExperimentArtifactRow.name == name)))
        if len(rows) != 1 or rows[0].size_bytes > 32 * 1024 ** 2 or rows[0].media_type != 'application/json':
            raise HTTPException(409, 'Batch response trace is absent or exceeds the supported size')
        artifact = rows[0]
        summary = execution['summary']['sides'][side]
        if summary.get('hil_trace_artifact') != name or summary.get('hil_trace_sha256') != artifact.sha256:
            raise HTTPException(409, 'Batch trace differs from its sealed summary')
        raw = experiment_jobs.artifact_path(db, artifact).read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise HTTPException(409, 'Batch trace content hash differs')
        try:
            trace = json.loads(raw)
            frames = trace['frames']
            if (trace['schema_version'] != 'hil-demo-trace/1' or trace['side'] != side
                    or trace['execution_target'] != 'software_dut' or not isinstance(frames, list) or not frames):
                raise ValueError('trace identity')
            if execution.get('comparison_kind') == 'same_fault_dut_ab' and (
                    trace.get('comparison_kind') != 'same_fault_dut_ab' or trace.get('fault') != execution['summary']['fault']
                    or canonical_bytes(trace.get('dut_profile')) != canonical_bytes(execution['dut_profiles'][side])):
                raise ValueError('trace policy binding')
            hil_demo._snapshot_scene(trace, execution.get('scene_profile'))
            previous, degraded, recovered, peak_age, peak_brake = -1., None, None, 0., 0.
            states = set()
            for frame in frames:
                time_s, age, brake = frame['time_s'], frame['input_age_ms'], frame['command']['brake']
                if (any(type(number) not in (int, float) or not math.isfinite(number) for number in (time_s, age, brake))
                        or not previous < time_s <= 18 or age < 0 or not 0 <= brake <= 1
                        or frame['dut_state'] not in {'tracking', 'degraded', 'recovering'}):
                    raise ValueError('response sample')
                previous = time_s
                states.add(frame['dut_state'])
                peak_age, peak_brake = max(peak_age, age), max(peak_brake, brake)
                if degraded is None and frame['dut_state'] == 'degraded':
                    degraded = time_s
                if degraded is not None and recovered is None and frame['dut_state'] == 'tracking':
                    recovered = time_s
            result[side] = dict(trace_sha256=artifact.sha256, response_count=len(frames), dut_states=sorted(states),
                first_degraded_s=degraded, first_recovered_s=recovered, peak_input_age_ms=peak_age, peak_brake=peak_brake)
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(409, 'Retained batch response trace is invalid') from error
    return dict(schema_version='hil-sim-batch-result/1', execution_id=execution['id'],
                manifest_hash=execution['manifest_hash'], sides=result, verification_status='not_a_test_verdict')


def _sync(db, session, value):
    """Reconcile receipts without dispatching. Preserve a completed cancel race."""
    for item in value['items']:
        if not item['attempts']:
            continue
        attempt = item['attempts'][-1]
        if not attempt['execution_id'] or attempt['status'] in beta.TERMINAL:
            continue
        execution, _ = beta._execution(session, value['project_id'], attempt['execution_id'], item['test'])
        attempt.update(status=execution['status'], execution=beta._execution_metadata(execution),
                       finished_at=execution['finished_at'], outcome_uncertain=execution['outcome_uncertain'])
        item['status'] = execution['status']
        if execution['status'] == 'completed' and not execution['outcome_uncertain']:
            try:
                attempt['result'] = _trace_summary(db, session, execution)
            except Exception as error:
                raise HTTPException(409, 'Completed batch response evidence could not be verified') from error
        elif execution['status'] in beta.TERMINAL:
            attempt['error'] = 'Execution ' + execution['status'] + '; inspect its original receipt'
            value['error'] = attempt['error']
            if value['status'] == 'running':
                value['status'] = 'stopped'
        if execution['outcome_uncertain']:
            value['status'] = 'stopped'
            value['error'] = 'Execution outcome is uncertain; no batch retry or following item was submitted'
    if value['status'] == 'stopping' and not any(item['status'] in ACTIVE for item in value['items']):
        value['status'] = 'cancelled'
    if value['status'] == 'running' and not _unfinished(value):
        value['status'] = 'completed'


def advance(db, registry):
    """Worker-only tick. Each batch owns at most one nonterminal child job."""
    with db.read_session() as session:
        projects = set(session.scalars(select(Row.project_id).where(Row.kind == BATCH)))
        identities = [(project, value['id']) for project in projects for value in beta._values(session, project, BATCH)
                      if value['status'] in RUNNING]
    for project, identity in identities:
        try:
            with jobs._write(db) as session:
                value = _batch(session, project, identity)
                if value['status'] not in RUNNING:
                    continue
                original = canonical_bytes(value)
                _sync(db, session, value)
                # A completed receipt must survive any later enqueue failure.
                # Otherwise explicit resume could mistake its stale queued item
                # for unfinished work and execute the completed condition again.
                if canonical_bytes(value) != original:
                    _save(session, value)
            with jobs._write(db) as session:
                # Cancellation or another coordinator may win between the two
                # transactions. Re-read and fence before creating any child.
                value = _batch(session, project, identity)
                if value['status'] != 'running' or any(item['status'] in ACTIVE for item in value['items']):
                    continue
                item = next(iter(_unfinished(value)), None)
                if item is None or item['status'] != 'pending':
                    continue
                prepared = _input(session, value, item)
                metadata = prepared.to_payload()['handler']
                handler = registry.get(metadata['id'])
                current = ExperimentHandlerMetadata.model_validate(handler.metadata()).model_dump(mode='json') if handler else None
                if current != metadata or current['status'] != 'available':
                    raise HTTPException(422, 'Pinned batch handler capabilities changed; no execution was queued')
                attempt_number = len(item['attempts']) + 1
                key = f'hil-batch:{identity}:{item["index"]}:{attempt_number}'
                # Both helpers are transaction-scoped. The final state check,
                # child queue insert, ordinary test link and membership commit
                # together; cancellation/parallel ticks cannot cross this fence.
                experiment = experiment_jobs.create_prepared_row(session, prepared, key)
                execution = experiment_jobs.submit_row(session, registry, experiment['id'], prepared.input_hash, key)
                detail, _ = beta._execution(session, project, execution.id, item['test'])
                link_id = 'hilrun_' + digest([item['test_ref']['id'], item['test_ref']['revision'], execution.id])[:48]
                beta._put(session, project, beta.LINK, dict(schema_version=beta.SCHEMAS[beta.LINK], id=link_id,
                    revision=1, project_id=project, test_ref=item['test_ref'], execution_id=execution.id,
                    input_hash=execution.input_hash, created_at=utcnow(), author=value['author']))
                item['attempts'].append(dict(attempt=attempt_number, status=execution.status, execution_id=execution.id,
                    created_at=utcnow(), finished_at=None, error=None, outcome_uncertain=False,
                    execution=beta._execution_metadata(detail), result=None))
                item['status'] = execution.status
                _save(session, value)
        except Exception as error:
            # The child insert and membership have rolled back together. Stop once;
            # never turn a transient exception into an automatic new execution.
            logging.getLogger(__name__).exception('HIL batch advancement stopped')
            with jobs._write(db) as session:
                value = _batch(session, project, identity)
                if value['status'] in RUNNING:
                    value['status'] = 'stopped'
                    value['error'] = ('Pinned batch input or execution evidence failed its integrity check' if isinstance(error, HTTPException)
                                      and error.status_code == 409 else 'Batch dispatch failed; inspect configuration before explicit resume')
                    value['resume_blocked'] = isinstance(error, HTTPException) and error.status_code == 409
                    _save(session, value)


def register_hil_batch_routes(api, db, registry):
    @api.get('/hil-sim/batches', tags=['HIL batches'])
    def listing(request: Request, project_id: str = 'default', limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            values = beta._values(session, project_id, BATCH)
            return dict(schema_version='hil-sim-batches/1', items=[_public(session, _batch(session, project_id, value['id']))
                for value in values[offset:offset + limit]], next_offset=offset + limit if len(values) > offset + limit else None)

    @api.post('/hil-sim/batches', status_code=201, tags=['HIL batches'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def create(body: HilBatchCreate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, request_hash, prior = beta._operation(session, body.project_id, 'batch-create', idempotency_key, body)
            if prior:
                return _public(session, prior, live=False)
            tests = [beta._test(session, body.project_id, reference) for reference in body.tests]
            now = utcnow()
            value = dict(schema_version=beta.SCHEMAS[BATCH], id=uid('hilbatch'), revision=1, project_id=body.project_id,
                name=body.name, note=body.note, created_at=now, updated_at=now, status='draft', error=None, resume_blocked=False,
                author=dict(user_label=principal.user_label, role=principal.role), items=[dict(index=index, test_ref=beta._ref(test),
                    test=test, status='pending', attempts=[], input_hash=None, resource_id=None) for index, test in enumerate(tests)])
            value['definition_sha256'] = digest(_definition(value))
            value = beta._put(session, body.project_id, BATCH, value)
            beta._remember(session, body.project_id, operation, request_hash, BATCH, value)
            return _public(session, value, live=False)

    @api.get('/hil-sim/batches/{identity}', tags=['HIL batches'])
    def get(identity: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return _public(session, _batch(session, project_id, identity))

    @api.post('/hil-sim/batches/{identity}/preview', tags=['HIL batches'],
              openapi_extra={'x-axiom-min-role': 'Engineer', 'x-axiom-read-only': True})
    def preview(identity: str, body: HilBatchAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        with db.read_session() as session:
            value = _batch(session, body.project_id, identity)
            _expected(value, body.expected_revision)
            if value['status'] != 'draft':
                reason = _resume_reason(session, value)
                if reason:
                    raise HTTPException(409, reason)
        review, _ = _prepare(db, registry, value)
        with db.read_session() as session:
            _expected(_batch(session, body.project_id, identity), body.expected_revision)
        return review

    def submit_action(identity, body, request, key, action):
        project_authorize(request, db, body.project_id, 'Engineer')
        scope = 'batch-' + action + ':' + identity
        with db.read_session() as session:
            _, _, prior = beta._operation(session, body.project_id, scope, key, body)
            if prior:
                return _public(session, prior, live=False)
            value = _batch(session, body.project_id, identity)
            _expected(value, body.expected_revision)
            if action == 'start' and value['status'] != 'draft':
                raise HTTPException(409, 'Only a draft batch can start')
            if action == 'resume' and (reason := _resume_reason(session, value)):
                raise HTTPException(409, reason)
        review, prepared = _prepare(db, registry, value)
        if review['preview_hash'] != body.preview_hash:
            raise HTTPException(409, 'Batch preparation differs from the reviewed preview')
        with jobs._write(db) as session:
            operation, request_hash, prior = beta._operation(session, body.project_id, scope, key, body)
            if prior:
                return _public(session, prior, live=False)
            value = _batch(session, body.project_id, identity)
            _expected(value, body.expected_revision)
            if action == 'resume' and (reason := _resume_reason(session, value)):
                raise HTTPException(409, reason)
            for item in _unfinished(value):
                frozen = prepared[item['index']]
                if item['input_hash'] is None:
                    beta._put(session, body.project_id, INPUT, dict(schema_version=beta.SCHEMAS[INPUT],
                        id=identity + '_' + str(item['index']), revision=1, project_id=body.project_id,
                        definition_sha256=value['definition_sha256'], test_ref=item['test_ref'], prepared=frozen.to_payload()))
                    item['input_hash'] = frozen.input_hash
                    item['resource_id'] = frozen.to_payload()['handler']['resource_id']
                item['status'] = 'pending'
            value['status'], value['error'] = 'running', None
            value = _save(session, value)
            beta._remember(session, body.project_id, operation, request_hash, BATCH, value)
            return _public(session, value, live=False)

    @api.post('/hil-sim/batches/{identity}/start', status_code=202, tags=['HIL batches'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def start(identity: str, body: HilBatchSubmit, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        return submit_action(identity, body, request, idempotency_key, 'start')

    @api.post('/hil-sim/batches/{identity}/resume', status_code=202, tags=['HIL batches'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def resume(identity: str, body: HilBatchSubmit, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        return submit_action(identity, body, request, idempotency_key, 'resume')

    @api.post('/hil-sim/batches/{identity}/cancel', tags=['HIL batches'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def cancel(identity: str, body: HilBatchAction, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, request_hash, prior = beta._operation(session, body.project_id, 'batch-cancel:' + identity, idempotency_key, body)
            if prior:
                return _public(session, prior, live=False)
            value = _batch(session, body.project_id, identity)
            _expected(value, body.expected_revision)
            if value['status'] not in {'draft', 'running', 'stopped'}:
                raise HTTPException(409, 'Batch is not available for cancellation')
            for item in value['items']:
                if item['attempts'] and item['attempts'][-1]['execution_id']:
                    row = jobs._row(session, item['attempts'][-1]['execution_id'])
                    if row.status not in beta.TERMINAL:
                        jobs.cancel_job_row(session, row)
                if item['status'] == 'pending':
                    item['status'] = 'cancelled'
            value['status'] = 'stopping'
            _sync(db, session, value)
            value = _save(session, value)
            beta._remember(session, body.project_id, operation, request_hash, BATCH, value)
            return _public(session, value, live=False)

    @api.get('/hil-sim/batches/{identity}/export', tags=['HIL batches'])
    def export(identity: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            value = _batch(session, project_id, identity)
            tests = []
            for item in value['items']:
                ids = {attempt['execution_id'] for attempt in item['attempts'] if attempt['execution_id']}
                rows = []
                for execution_id in sorted(ids):
                    execution, _ = beta._execution(session, project_id, execution_id, item['test'])
                    rows.append(dict(test_ref=item['test_ref'], execution=beta._execution_metadata(execution),
                        artifacts=[{key: artifact[key] for key in ('id', 'name', 'sha256', 'size_bytes', 'media_type')}
                                   for artifact in execution['artifacts']]))
                notes = [note for note in beta._observations(session, project_id) if note['execution_id'] in ids
                         and (note.get('test_ref') is None or note['test_ref'] == item['test_ref'])]
                tests.append(dict(schema_version='hil-sim-test-export/1', test=item['test'], executions=rows, observations=notes,
                                  execution_target='software_dut', verification_status='not_a_test_verdict'))
            result = dict(schema_version='hil-sim-batch-export/1', exported_at=utcnow(), batch=_public(session, value), tests=tests,
                          execution_target='software_dut', verification_status='not_a_test_verdict')
            raw = canonical_bytes(result)
            return Response(raw, media_type='application/json', headers={'X-Content-SHA256': hashlib.sha256(raw).hexdigest(),
                'Content-Disposition': f'attachment; filename="{identity}-r{value["revision"]}.json"'})
