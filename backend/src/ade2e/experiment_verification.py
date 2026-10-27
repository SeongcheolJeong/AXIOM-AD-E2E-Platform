"""Typed plan -> fixed execution receipts -> explicit gaps and criterion results.

This evaluator establishes bounded authored/reference observer criteria only.
Sensor/ISP/driving receipts remain contextual evidence, never safety passes.
"""
import hashlib
import json
import math
from fastapi import Header, HTTPException, Query, Request, Response
from sqlalchemy import select
from . import experiment_jobs, jobs
from .experiment_models import ExperimentExecutionRow, ExperimentArtifactRow
from .experiment_verification_models import (ExperimentVerificationPlanRow as PlanRow,
    ExperimentVerificationRevisionRow as RevisionRow, ExperimentVerificationEvaluationRow as EvaluationRow)
from .experiment_verification_schemas import ExperimentVerificationCreate, ExperimentVerificationEvaluate
from .job_models import JobRow
from .service import canonical_bytes, digest, uid, utcnow
from .team_access import project_authorize
from .city_motion import FIDELITIES as CITY_FIDELITIES

EVALUATOR_VERSION = 'typed-experiment-observers/2'

def _load(session, identity, revision=None):
    plan = session.get(PlanRow, identity)
    if plan is None:
        raise HTTPException(404, 'Experiment verification plan not found')
    selected = plan.revision if revision is None else revision
    row = session.get(RevisionRow, (identity, selected))
    if row is None:
        raise HTTPException(404, 'Plan revision not found')
    try:
        value = json.loads(row.manifest)
        request = ExperimentVerificationCreate.model_validate(value['request'])
        if (hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash or value['id'] != identity
                or value['revision'] != selected or request.project_id != plan.project_id
                or digest(request.model_dump(mode='json')) != row.request_hash):
            raise ValueError('Plan revision hash/identity differs')
        return plan, row, value, request
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, 'Plan revision failed its integrity check') from exc

def _key(project, key, domain):
    return experiment_jobs._key(project, key, domain)

def create(db, body, key, *, identity=None):
    request_key = _key(body.project_id, key, 'experiment-plan-revision')
    request_hash = digest(body.model_dump(mode='json'))
    with jobs._write(db) as session:
        previous = session.scalar(select(RevisionRow).where(RevisionRow.request_key == request_key))
        if previous:
            if previous.request_hash != request_hash or (identity and previous.plan_id != identity):
                raise HTTPException(409, 'Idempotency-Key identifies another plan revision')
            return _load(session, previous.plan_id, previous.revision)[2] | {'manifest_hash': previous.manifest_hash}
        if identity is None:
            plan = PlanRow(id=uid('experiment_plan'), project_id=body.project_id, revision=1)
            session.add(plan)
        else:
            plan, _, _, _ = _load(session, identity)
            if plan.project_id != body.project_id:
                raise HTTPException(422, 'A plan revision cannot change its project boundary')
            plan.revision += 1
        value = {'schema_version': 1, 'id': plan.id, 'revision': plan.revision, 'created_at': utcnow(),
                 'request': body.model_dump(mode='json'), 'evaluator_version': EVALUATOR_VERSION,
                 'evidence_scope': 'context_only' if not body.criteria else 'bounded_observer_criteria',
                 'is_safety_validation': False}
        content = canonical_bytes(value)
        row = RevisionRow(plan_id=plan.id, revision=plan.revision, manifest=content,
            manifest_hash=hashlib.sha256(content).hexdigest(), request_key=request_key, request_hash=request_hash)
        session.add(row)
        return value | {'manifest_hash': row.manifest_hash}

def _object_metric(summary, criterion):
    candidates = [item for item in summary.get('observers', []) if isinstance(item, dict)
                  and item.get('observer', {}).get('kind') == criterion.metric
                  and item.get('observer', {}).get('actor_id') == criterion.actor_id]
    if len(candidates) != 1:
        return {'status': 'unevaluated', 'reason': 'required_observer_missing_or_ambiguous', 'denominator': 1, 'evaluated': 0}
    item = candidates[0]
    if item.get('status') not in {'observed', 'passed', 'failed'}:
        return {'status': 'unevaluated', 'reason': item.get('reason') or 'observer_unevaluated', 'denominator': 1, 'evaluated': 0}
    unit = {'m/s²': 'm/s2', 'm/s³': 'm/s3'}.get(item.get('units'), item.get('units'))
    return {'status': 'observed', 'value': item.get('value'), 'unit': unit, 'denominator': 1, 'evaluated': 1,
            'source_evaluator': item.get('evaluator_version'), 'source_samples': summary.get('sample_count')}

PLANNING_METRICS = {'collision': 'collision', 'minimum_distance': 'distance', 'minimum_ttc': 'ttc',
    'lane_departure': 'lane_departure', 'maximum_speed': 'speed', 'maximum_acceleration': 'acceleration',
    'maximum_jerk': 'jerk', 'goal_arrival': 'goal_arrival', 'maximum_trajectory_error': 'trajectory_error'}

def _planning_metrics(db, session, execution_id, criteria):
    artifacts = list(session.scalars(select(ExperimentArtifactRow).where(
        ExperimentArtifactRow.job_id == execution_id, ExperimentArtifactRow.name == 'planning-trace.ndjson')))
    if len(artifacts) != 1:
        return {criterion.metric: {'status': 'unevaluated', 'reason': 'full_planning_trace_missing_or_ambiguous',
                                  'denominator': 1, 'evaluated': 0} for criterion in criteria}
    path = experiment_jobs.artifact_path(db, artifacts[0])
    accumulated = {criterion.metric: {'denominator': 0, 'evaluated': 0, 'aggregate': None, 'reasons': set()} for criterion in criteria}
    with path.open('rb') as stream:
        for line in stream:
            if len(line) > experiment_jobs.MANIFEST_LIMIT:
                raise HTTPException(409, 'Planning trace line exceeds its supported contract')
            try:
                frame = json.loads(line)
                metrics = frame['metrics']
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(409, 'Planning native trace failed its metric contract') from exc
            for criterion in criteria:
                item = metrics.get(PLANNING_METRICS[criterion.metric])
                state = accumulated[criterion.metric]
                state['denominator'] += 1
                if not isinstance(item, dict) or item.get('status') != 'evaluated' or item.get('value') is None:
                    state['reasons'].add((item or {}).get('reason', 'metric_missing_or_unevaluated') if isinstance(item, dict) else 'metric_missing')
                    continue
                value = item['value']
                if item.get('unit') != criterion.unit or (criterion.unit == 'boolean' and type(value) is not bool) or (
                        criterion.unit != 'boolean' and (type(value) not in {int, float} or not math.isfinite(value))):
                    state['reasons'].add('metric_unit_or_value_type_mismatch')
                    continue
                state['evaluated'] += 1
                aggregate = state['aggregate']
                if criterion.unit == 'boolean':
                    state['aggregate'] = bool(aggregate) or value
                elif criterion.metric in {'minimum_distance', 'minimum_ttc'}:
                    state['aggregate'] = value if aggregate is None else min(aggregate, value)
                elif criterion.metric in {'maximum_acceleration', 'maximum_jerk'}:
                    state['aggregate'] = abs(value) if aggregate is None else max(aggregate, abs(value))
                else:
                    state['aggregate'] = value if aggregate is None else max(aggregate, value)
    result = {}
    for criterion in criteria:
        state = accumulated[criterion.metric]
        value = state.pop('aggregate')
        reasons = sorted(str(reason) for reason in state.pop('reasons') if reason is not None)
        if not state['evaluated']:
            result[criterion.metric] = state | {'status': 'unevaluated', 'reason': reasons or ['empty_trace'], 'unit': criterion.unit}
            continue
        result[criterion.metric] = state | {'status': 'observed', 'value': value, 'unit': criterion.unit, 'missing_reasons': reasons,
                                          'source_evaluator': 'reference-planning-replay/1'}
    return result

def _criterion_result(observation, criterion):
    result = {'criterion': criterion.model_dump(mode='json'), 'observation': observation, 'status': 'unevaluated'}
    if observation.get('status') != 'observed':
        return result
    denominator, evaluated = observation.get('denominator', 0), observation.get('evaluated', 0)
    if not denominator or evaluated / denominator < criterion.required_evaluated_fraction:
        result['reason'] = 'insufficient_evaluated_fraction'
        return result
    value = observation.get('value')
    if observation.get('unit') != criterion.unit or (criterion.unit == 'boolean' and type(value) is not bool) or (
            criterion.unit != 'boolean' and (type(value) not in {int, float} or not math.isfinite(value))):
        result['reason'] = 'metric_unit_or_value_type_mismatch'
        return result
    operations = {'eq': lambda a,b:a==b, 'lt':lambda a,b:a<b, 'lte':lambda a,b:a<=b, 'gt':lambda a,b:a>b, 'gte':lambda a,b:a>=b}
    result['status'] = 'passed' if operations[criterion.operator](value, criterion.threshold) else 'failed'
    return result

def evaluate(db, identity, body, key):
    with db.read_session() as session:
        plan, revision, plan_value, spec = _load(session, identity, body.plan_revision)
        project, plan_hash = plan.project_id, revision.manifest_hash
    request_key = _key(project, key, 'experiment-plan-evaluation')
    request_hash = digest({'plan_id': identity, 'request': body.model_dump(mode='json')})
    with db.read_session() as session:
        previous = session.scalar(select(EvaluationRow).where(EvaluationRow.request_key == request_key))
        if previous:
            if previous.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key identifies another evidence evaluation')
            return _evaluation_value(previous)
        entries, input_hashes = [], set()
        for execution_id in body.execution_ids:
            subject = session.get(ExperimentExecutionRow, execution_id)
            if subject is None:
                entries.append({'execution_id': execution_id, 'status': 'missing_input', 'reason': 'execution_receipt_not_found'})
                continue
            if subject.project_id != project:
                raise HTTPException(404, 'Project evidence resource not found')
            row = session.scalar(select(JobRow).where(JobRow.id == execution_id))
            receipt = experiment_jobs.detail(session, row)
            entry = {'execution_id': execution_id, 'input_hash': row.input_hash, 'execution_status': row.status,
                     'manifest_hash': receipt.manifest_hash, 'fidelity': receipt.fidelity, 'outcome_uncertain': row.outcome_uncertain}
            input_hashes.add(row.input_hash)
            if receipt.experiment_kind != spec.experiment_kind:
                entry.update(status='unsupported', reason='experiment_kind_differs_from_plan')
            elif row.status in {'failed', 'interrupted'}:
                entry.update(status='unsupported' if row.failure_http_status == 422 and not row.dispatched_at else 'infrastructure_failure',
                             reason=row.error or 'execution_did_not_complete')
            elif row.status != 'completed':
                entry.update(status='unevaluated', reason='execution_not_completed')
            elif row.outcome_uncertain:
                entry.update(status='unevaluated', reason='execution_outcome_uncertain')
            elif receipt.fidelity not in spec.allowed_fidelities:
                entry.update(status='unsupported', reason='execution_fidelity_outside_plan_scope')
            elif not spec.criteria:
                entry.update(status='context_only', reason='no_accepted_metric_evaluator_for_this_scope')
            else:
                observations = _planning_metrics(db, session, row.id, spec.criteria) if spec.experiment_kind == 'planning_replay' else None
                city_observations = None
                if spec.experiment_kind == 'object_sim' and receipt.fidelity in CITY_FIDELITIES:
                    from .experiment_verification_city import observations as native_city_observations
                    city_observations = native_city_observations(db,session,row,receipt,spec.criteria)
                criteria = [_criterion_result(city_observations[(item.metric,item.actor_id)] if city_observations is not None else
                            observations[item.metric] if observations is not None else _object_metric(receipt.summary or {}, item), item)
                            for item in spec.criteria]
                entry['criteria'] = criteria
                entry['status'] = 'sut_failure' if any(item['status'] == 'failed' for item in criteria) else (
                    'unevaluated' if any(item['status'] == 'unevaluated' for item in criteria) else 'passed')
            entries.append(entry)
    counts = {status: sum(entry['status'] == status for entry in entries) for status in
              ('passed', 'sut_failure', 'missing_input', 'unsupported', 'infrastructure_failure', 'unevaluated', 'context_only')}
    verdict = 'failed' if counts['sut_failure'] else 'passed' if counts['passed'] == len(entries) else 'not_evaluated'
    value = {'schema_version': 1, 'id': uid('experiment_evaluation'), 'created_at': utcnow(), 'plan_id': identity,
        'plan_revision': body.plan_revision, 'plan_manifest_hash': plan_hash, 'project_id': project,
        'evaluator_version': EVALUATOR_VERSION, 'execution_denominator': len(entries), 'counts': counts, 'verdict': verdict,
        'entries': entries, 'distinct_input_hashes': len(input_hashes), 'evidence_scope': plan_value['evidence_scope'],
        'is_safety_validation': False, 'limitations': ['Requested execution count is not independent scene diversity.',
            'Authored/reference and bounded native engine criteria do not establish physical sensor, vehicle or road safety validity.',
            'Native collision-sensor contacts and sampled geometry collisions are different metric contracts.']}
    content = canonical_bytes(value)
    if len(content) > experiment_jobs.MANIFEST_LIMIT:
        raise HTTPException(413, 'Typed evaluation receipt exceeds 4 MiB')
    with jobs._write(db) as session:
        previous = session.scalar(select(EvaluationRow).where(EvaluationRow.request_key == request_key))
        if previous:
            if previous.request_hash != request_hash:
                raise HTTPException(409, 'Idempotency-Key identifies another evidence evaluation')
            return _evaluation_value(previous)
        row = EvaluationRow(id=value['id'], plan_id=identity, revision=body.plan_revision, manifest=content,
            manifest_hash=hashlib.sha256(content).hexdigest(), request_key=request_key, request_hash=request_hash)
        session.add(row)
        return value | {'manifest_hash': row.manifest_hash}

def _evaluation_value(row):
    if hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
        raise HTTPException(409, 'Typed evaluation receipt failed its content hash')
    value = json.loads(row.manifest)
    if value['id'] != row.id or value['plan_id'] != row.plan_id or value['plan_revision'] != row.revision:
        raise HTTPException(409, 'Typed evaluation receipt differs from its stored identity')
    return value | {'manifest_hash': row.manifest_hash}

def register_experiment_verification_routes(api, db):
    prefix = '/experiment-verification-plans'
    def authorize(request, identity, role='Viewer'):
        with db.read_session() as session:
            plan, _, _, _ = _load(session, identity)
            project = plan.project_id
        project_authorize(request, db, project, role)

    @api.get(prefix + '/capabilities', tags=['experiment-verification'])
    def capabilities():
        from .experiment_verification_city import PROFILE_VERSION, METRICS
        return {'schema_version': 1, 'evaluator_version': EVALUATOR_VERSION,
                'metric_kinds': ['object_sim', 'planning_replay'], 'context_only_kinds': ['sensor_capture', 'isp_compare', 'driving_open_loop', 'hil_demo', 'neural_reconstruction', 'neural_render', 'neural_source_import', 'neural_perception'],
                'profiles':[{'id':PROFILE_VERSION,'kind':'object_sim','fidelity':'native_carla_object_physics',
                    'accepted_fidelities':sorted(CITY_FIDELITIES),'replay_policy':'replay motion and mixed-replay contact criteria are diagnostic only; physical actors retain individual route metrics',
                    'metrics':METRICS,'all_actor_metric':'native_contact','required_artifacts':['city-scene.json','city-evaluation.json','map.xodr'],
                    'source_policy':'sealed exact controller/scenario/map revisions and original artifact hashes',
                    'collision_note':'native_contact uses collision sensors; collision is a separate sampled observer metric'}],
                'is_safety_validation': False, 'max_executions': 128}

    @api.post(prefix, status_code=201, tags=['experiment-verification'])
    def plan_create(body: ExperimentVerificationCreate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        project_authorize(request, db, body.project_id, 'Engineer')
        return create(db, body, idempotency_key)

    @api.get(prefix, tags=['experiment-verification'])
    def plans(request: Request, project_id: str = 'default', limit: int = Query(default=50, ge=1, le=100)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            rows = session.scalars(select(PlanRow).where(PlanRow.project_id == project_id).order_by(PlanRow.id).limit(limit))
            return {'items': [_load(session, row.id)[2] | {'manifest_hash': _load(session, row.id)[1].manifest_hash} for row in rows]}

    @api.get(prefix + '/{plan_id}', tags=['experiment-verification'])
    def get(plan_id: str, request: Request, revision: int | None = Query(default=None, ge=1)):
        authorize(request, plan_id)
        with db.read_session() as session:
            _, row, value, _ = _load(session, plan_id, revision)
            evaluations = session.scalars(select(EvaluationRow).where(EvaluationRow.plan_id == plan_id).order_by(EvaluationRow.id))
            return value | {'manifest_hash': row.manifest_hash,
                            'evaluations': sorted((_evaluation_value(item) for item in evaluations),
                                                  key=lambda item: (item['created_at'], item['id']))}

    @api.post(prefix + '/{plan_id}/revisions', status_code=201, tags=['experiment-verification'])
    def revise(plan_id: str, body: ExperimentVerificationCreate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        authorize(request, plan_id, 'Engineer')
        project_authorize(request, db, body.project_id, 'Engineer')
        return create(db, body, idempotency_key, identity=plan_id)

    @api.post(prefix + '/{plan_id}/evaluations', status_code=201, tags=['experiment-verification'],
              openapi_extra={'x-axiom-min-role': 'Reviewer'},
              description='Capture typed criteria against sealed executions. Native city profile verifies original scene/evaluation/map hashes and frozen controller inputs; native contacts are distinct from sampled overlap. Missing data, infrastructure failure and SUT failure remain separate. Requires Reviewer and Idempotency-Key.')
    def evaluation(plan_id: str, body: ExperimentVerificationEvaluate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        authorize(request, plan_id, 'Reviewer')
        return evaluate(db, plan_id, body, idempotency_key)

    @api.get(prefix + '/{plan_id}/evaluations/{evaluation_id}/export', tags=['experiment-verification'])
    def export(plan_id: str, evaluation_id: str, request: Request):
        authorize(request, plan_id)
        with db.read_session() as session:
            row = session.get(EvaluationRow, evaluation_id)
            if row is None or row.plan_id != plan_id:
                raise HTTPException(404, 'Typed plan evaluation not found')
            _evaluation_value(row)
            return Response(row.manifest, media_type='application/json', headers={'X-Content-SHA256': row.manifest_hash,
                            'Content-Disposition': f'attachment; filename="{evaluation_id}.json"'})
