"""Same retained input, two reference controllers, and repeatable request checks.

Uses the existing frozen sequence, replay adapter and durable experiment queue.
No sensor reconstruction, learned-model inference or reviewer approval is implied.
"""
import copy
import hashlib
import json
from threading import RLock
from typing import Literal

from fastapi import Header, HTTPException, Request
from pydantic import Field
from sqlalchemy import select

from . import cases, experiment_jobs, log_sim
from .base_schemas import StrictModel
from .experiment_models import ExperimentArtifactRow, ExperimentExecutionRow
from .experiment_schemas import ExperimentCreate
from .job_models import JobRow
from .project_recordings import authorize_recording_source
from .planning_replay import world_objects
from .recording_models import RecordingMessageRow, RecordingRow
from .recordings import read_message_bytes
from .replay_spec_schemas import ReferenceController, ReplaySpecCreate
from .replay_specs import create_replay_spec
from .scene_sequence_schemas import PlanningEgo, SceneSequenceCreate
from .scene_sequences import create_scene_sequence, get_scene_sequence, prepare_sequence
from .service import canonical_bytes, digest, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Catalog
from .team_access import project_authorize

COMPARISON = 'log-controller-comparison'
REGRESSION = 'log-controller-regression'
RUN = 'log-controller-regression-run'
LOCK = RLock()
PROFILE = Literal['balanced', 'cautious', 'responsive']
PROFILES = {
    'balanced': {'id': 'balanced', 'label': '기준 · 균형', 'controller': ReferenceController().model_dump()},
    'cautious': {'id': 'cautious', 'label': '후보 · 여유 거리', 'controller': ReferenceController(
        time_headway_s=2.5, stop_buffer_m=4., cruise_speed_mps=6.).model_dump()},
    'responsive': {'id': 'responsive', 'label': '후보 · 짧은 간격', 'controller': ReferenceController(
        time_headway_s=1., stop_buffer_m=1., cruise_speed_mps=10.).model_dump()},
}
LIMITATIONS = [
    'GT/recorded object inputs; geometric-headway/1 is a reference controller, not a learned model.',
    'Open-loop requests are not applied to the vehicle. Original camera pixels and ego trajectory remain unchanged.',
    'Case selects an investigation window; both controllers execute the entire retained source sequence.',
    'Request-limit checks are diagnostic regression checks, not safety acceptance or reviewer approval.',
]


class Selection(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=100)
    case_id: str = Field(min_length=1, max_length=100)
    case_revision: int = Field(ge=1, strict=True)
    baseline_profile: PROFILE = 'balanced'
    candidate_profile: PROFILE = 'cautious'


class Submit(Selection):
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class ProjectAction(StrictModel):
    project_id: str = Field(default='default', min_length=1, max_length=64)


class RegressionCreate(ProjectAction):
    name: str = Field(min_length=1, max_length=180)
    max_deceleration_request_mps2: float = Field(default=5., ge=0, le=15, strict=True, allow_inf_nan=False)
    max_target_speed_mps: float = Field(default=10., ge=0, le=50, strict=True, allow_inf_nan=False)


def _put(session, project, kind, identity, value):
    prior = log_sim._catalog(session, project, kind, identity)
    if prior and prior['definition'] == value:
        return
    revision = prior['revision'] + 1 if prior else 1
    manifest = dict(schema_version=kind+'/1', project_id=project, kind=kind, id=identity,
                    revision=revision, definition=value)
    session.add(Catalog(project_id=project, kind=kind, id=identity, revision=revision,
                       manifest=canonical_bytes(manifest), manifest_hash=digest(manifest)))
    session.flush()


def _get(session, project, kind, identity):
    row = log_sim._catalog(session, project, kind, identity)
    if row is None:
        raise HTTPException(404, 'Log Sim workflow not found')
    return row['definition']


def _values(session, project, kind):
    ids = set(session.scalars(select(Catalog.id).where(Catalog.project_id == project, Catalog.kind == kind)))
    return sorted((_get(session, project, kind, identity) for identity in ids),
                  key=lambda value: value['created_at'], reverse=True)


def _access(request, db, project, value, role='Viewer'):
    project_authorize(request, db, project, role)
    if value['source_kind'] in {'nuscenes', 'recorded'}:
        authorize_recording_source(request, db, project, value['recording_ref']['id'])


def _prepare(db, body):
    return prepare_source(db, body.project_id, body.source_id,
        {'baseline': PROFILES[body.baseline_profile], 'candidate': PROFILES[body.candidate_profile]},
        body=body)


def prepare_source(db, project_id, source_id, profiles, *, body=None):
    """Read-only source pin shared by single-Case and batch replay workflows."""
    with db.read_session() as session:
        source = log_sim._source(session, project_id, source_id)
        pin = log_sim._case_pin(session, source, body) if body is not None else None
        case = cases._get(session, body.case_id, body.case_revision) if body is not None else None
        ref = source.get('recording_ref')
        if not ref:
            raise HTTPException(422, 'This source has no retained planning recording')
        rows = list(session.scalars(select(RecordingMessageRow).where(
            RecordingMessageRow.recording_id == ref['id'], RecordingMessageRow.channel_id == ref['ego_channel_id'])
            .order_by(RecordingMessageRow.log_time_key, RecordingMessageRow.ordinal)))
        if ref.get('ego_ordinals') is not None:
            rows = [row for row in rows if row.ordinal in ref['ego_ordinals']]
        if not 2 <= len(rows) <= 512:
            raise HTTPException(422, 'Same-log comparison requires 2..512 retained ego observations')
        ego = PlanningEgo.model_validate_json(read_message_bytes(db, session.get(RecordingRow, ref['id']), rows[0]))
        definition = SceneSequenceCreate(name='Log Sim retained source', project_id=project_id,
            source_type='recording', recording_id=ref['id'], ego_channel_id=ref['ego_channel_id'],
            object_channel_id=ref['objects_channel_id'], ego_ordinals=[row.ordinal for row in rows],
            world_frame=ego.frame_id, clock_domain='original-log-clock', object_max_age_ns='0',
            note='Entire retained Log Sim source; Case is an investigation window. Original RGB is context only.')
        try:
            snapshot = prepare_sequence(session, db, definition)
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error)) from error
        if any(frame['object_drop'] for frame in snapshot['frames']):
            raise HTTPException(422, 'Comparison requires exact object observations at every retained ego timestamp')
        selected = {item['ordinal'] for item in case['segment']['samples']} if case else {row.ordinal for row in rows}
        indices = [i for i, frame in enumerate(snapshot['frames']) if frame['ego_source']['ordinal'] in selected]
        if len(indices) != len(selected):
            raise HTTPException(409, 'Case samples are not entirely in the retained source')
        value = dict(schema_version='log-controller-preview/1', request=body.model_dump(exclude={'preview_hash'}) if body else None,
            source_hash=source['source_hash'], source_kind=source['kind'], recording_ref=ref,
            case_ref=pin, case_indices=indices, frame_count=len(rows), case_frame_count=len(indices),
            sequence_sha256=digest(snapshot), scope='full_source_sequence', mode='open_loop', learned_model=False,
            seed=42, adapter_id='reference-planning-replay', adapter_version='1',
            profiles=profiles,
            source_use_scope=snapshot.get('source_use_scope'), limitations=LIMITATIONS)
        return value | {'preview_hash': digest(value)}, definition


def _launch(db, registry, value):
    """Checkpoint each side; retrying the same request recovers partial submission."""
    for side in ('baseline', 'candidate'):
        if value['execution_ids'].get(side):
            continue
        key = value['id'] + ':' + side
        experiment = experiment_jobs.create(db, registry, ExperimentCreate(
            project_id=value['project_id'], name='Log Sim '+side, kind='planning_replay',
            adapter_id='reference-planning-replay', adapter_version='1',
            input={'replay_spec_id': value['replay_spec_ids'][side]}, seed=42), key)
        execution = experiment_jobs.submit(db, registry, experiment['id'], experiment['preview_hash'], key)
        value['execution_ids'][side] = execution.id
        with db.session.begin() as session:
            _put(session, value['project_id'], COMPARISON, value['id'], value)
    return value


def _trace(db, session, job_id, project):
    row = session.scalar(select(JobRow).where(JobRow.id == job_id))
    owner = session.get(ExperimentExecutionRow, job_id)
    if row is None or owner is None or owner.project_id != project:
        raise HTTPException(409, 'Retained comparison execution is missing')
    detail = experiment_jobs.detail(session, row).model_dump(mode='json')
    if detail['status'] != 'completed':
        return detail, None
    artifact = session.scalar(select(ExperimentArtifactRow).where(
        ExperimentArtifactRow.job_id == job_id, ExperimentArtifactRow.name == 'planning-trace.ndjson'))
    if artifact is None or artifact.size_bytes > 32*1024*1024:
        raise HTTPException(409, 'Complete retained planning trace unavailable')
    raw = experiment_jobs.artifact_path(db, artifact).read_bytes()
    if len(raw) != artifact.size_bytes or hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise HTTPException(409, 'Retained planning trace integrity check failed')
    return detail, [json.loads(line) for line in raw.splitlines()]


def _result(value, sequence, details, traces):
    frames = sequence.snapshot['frames']
    if any(detail['outcome_uncertain'] for detail in details.values()):
        return {'status': 'not_evaluated', 'reason': 'Execution outcome is uncertain'}
    if sequence.sha256 != value['sequence_sha256']:
        return {'status': 'not_comparable', 'reason': 'Frozen sequence identity differs'}
    for side in ('baseline', 'candidate'):
        summary = details[side]['summary']
        if (summary['sequence_sha256'] != value['sequence_sha256'] or summary['mode'] != 'open_loop'
                or summary['controller'] != value['profiles'][side]['controller'] or len(traces[side]) != len(frames)
                or summary['seed'] != value['seed'] or summary['kernel_version'] != 'reference-planning-replay/1'
                or details[side]['adapter_id'] != 'reference-planning-replay' or details[side]['adapter_version'] != '1'
                or summary['dynamics'] != details['baseline']['summary']['dynamics']):
            return {'status': 'not_comparable', 'reason': 'Frozen input, controller or complete trace differs'}
        for tick, frame in zip(traces[side], frames):
            if (tick['at_ns'] != frame['at_ns'] or tick['source_ego'] != frame['ego_source']
                    or tick['source_objects'] != frame['object_source'] or tick['ego'] != frame['ego']
                    or tick['logged_ego'] != frame['ego'] or tick['world_objects'] != world_objects(frame)
                    or tick['control_application'] != 'predicted_not_applied_open_loop'):
                return {'status': 'not_comparable', 'reason': 'Exact source observations differ'}
    samples = []
    origin = int(value['recording_ref']['time_origin_ns'])
    for i, (a, b) in enumerate(zip(traces['baseline'], traces['candidate'])):
        samples.append(dict(index=i, time_s=(int(a['at_ns'])-origin)/1e9, in_case=i in value['case_indices'],
            logged_speed_mps=a['logged_ego']['speed_mps'], baseline=a['control'], candidate=b['control']))
    changed = [sample for sample in samples if sample['baseline'] != sample['candidate']]
    metrics = []
    for name, label, unit, fn in [
        ('max_deceleration_request_mps2', '최대 감속 요청', 'm/s²', lambda controls: max(max(0., -x['acceleration_mps2']) for x in controls)),
        ('max_target_speed_mps', '최대 목표 속도', 'm/s', lambda controls: max(x['target_speed_mps'] for x in controls)),
        ('mean_target_speed_mps', '평균 목표 속도', 'm/s', lambda controls: sum(x['target_speed_mps'] for x in controls)/len(controls)),
    ]:
        a, b = (fn([sample[side] for sample in samples]) for side in ('baseline', 'candidate'))
        metrics.append(dict(name=name, label=label, unit=unit, baseline=a, candidate=b, delta=b-a))
    return dict(status='comparable', frame_count=len(samples), changed_request_count=len(changed),
        case_changed_request_count=sum(x['in_case'] for x in changed),
        first_changed_time_s=changed[0]['time_s'] if changed else None, metrics=metrics, samples=samples,
        output_hash=digest([{k: sample[k] for k in ('index', 'baseline', 'candidate')} for sample in samples]))


def _report(db, session, value):
    result = copy.deepcopy(value)
    details, traces = {}, {}
    for side, identity in value['execution_ids'].items():
        details[side], traces[side] = _trace(db, session, identity, value['project_id'])
    states = [detail['status'] for detail in details.values()]
    result['executions'] = {side: {k: detail[k] for k in ('id', 'status', 'error', 'manifest_hash', 'download_url')}
                            for side, detail in details.items()}
    result['result'] = None
    if len(states) == 2 and all(state == 'completed' for state in states):
        result['status'] = 'completed'
        result['result'] = _result(value, get_scene_sequence(session, value['sequence_id']), details, traces)
    elif any(state in {'failed', 'cancelled', 'interrupted'} for state in states):
        result['status'] = 'failed' if any(state in {'failed', 'interrupted'} for state in states) else 'cancelled'
        result['result'] = {'status': 'not_evaluated', 'reason': 'Both complete executions are required'}
    else:
        result['status'] = 'running' if any(state != 'queued' for state in states) else 'queued'
    result['report_hash'] = digest(result)
    return result


def _run_report(db, session, run, regression):
    comparison = _report(db, session, _get(session, run['project_id'], COMPARISON, run['comparison_id']))
    result = comparison['result']
    report = run | {'status': comparison['status'], 'verdict': 'not_evaluated', 'checks': [], 'matches_saved_outputs': None}
    if result and result['status'] == 'comparable':
        metrics = {item['name']: item['candidate'] for item in result['metrics']}
        report['checks'] = [dict(name=name, value=metrics[name], limit=limit, passed=metrics[name] <= limit)
                            for name, limit in regression['criteria'].items()]
        report['verdict'] = 'pass' if all(item['passed'] for item in report['checks']) else 'fail'
        report['matches_saved_outputs'] = result['output_hash'] == regression['saved_output_hash']
    return report


def register_log_sim_replay_routes(api, db, registry):
    @api.get('/log-sim/replay-profiles', tags=['Log Sim'])
    def profiles(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        return dict(items=list(PROFILES.values()), mode='open_loop', learned_model=False, limitations=LIMITATIONS)

    @api.post('/log-sim/comparisons/preview', tags=['Log Sim'], openapi_extra={'x-axiom-read-only': True})
    def preview(body: Selection, request: Request):
        project_authorize(request, db, body.project_id)
        with db.read_session() as session:
            source = log_sim._source(session, body.project_id, body.source_id)
            if source['kind'] in {'nuscenes', 'recorded'}:
                authorize_recording_source(request, db, body.project_id, source['recording_ref']['id'])
        return _prepare(db, body)[0]

    @api.post('/log-sim/comparisons', status_code=202, tags=['Log Sim'])
    def submit(body: Submit, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        project_authorize(request, db, body.project_id, 'Engineer')
        identity = 'logcmp_'+digest({'project': body.project_id, 'key': idempotency_key})[:32]
        payload = body.model_dump()
        with LOCK:
            with db.read_session() as session:
                old = log_sim._catalog(session, body.project_id, COMPARISON, identity)
            if old:
                value = old['definition']
                _access(request, db, body.project_id, value, 'Engineer')
                if value['request'] != payload:
                    raise HTTPException(409, 'Idempotency key identifies another comparison')
            else:
                p = preview(body, request)
                if p['preview_hash'] != body.preview_hash:
                    raise HTTPException(409, 'Source, Case or comparison settings changed; preview again')
                _, definition = _prepare(db, body)
                with db.session.begin() as session:
                    sequence = create_scene_sequence(session, db, definition, expected_snapshot_sha256=p['sequence_sha256'])
                    specs = {side: create_replay_spec(session, db, ReplaySpecCreate(name='Log Sim '+side,
                        project_id=body.project_id, sequence_id=sequence.id, sequence_sha256=sequence.sha256,
                        mode='open_loop', controller=profile['controller'], observers=['speed'], note=LIMITATIONS[1])).id
                        for side, profile in p['profiles'].items()}
                    value = {k: p[k] for k in ('source_hash', 'source_kind', 'recording_ref', 'case_ref', 'case_indices',
                        'frame_count', 'case_frame_count', 'sequence_sha256', 'profiles', 'source_use_scope', 'limitations')}
                    value.update(schema_version='log-controller-comparison/1', id=identity, project_id=body.project_id,
                        source_id=body.source_id, request=payload, created_at=utcnow(), sequence_id=sequence.id,
                        replay_spec_ids=specs, execution_ids={}, mode='open_loop', learned_model=False, seed=42)
                    _put(session, body.project_id, COMPARISON, identity, value)
            value = _launch(db, registry, value)
            with db.read_session() as session:
                return _report(db, session, value)

    @api.get('/log-sim/comparisons', tags=['Log Sim'])
    def comparisons(request: Request, project_id: str = 'default', source_id: str | None = None):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            values = [v for v in _values(session, project_id, COMPARISON)
                      if v.get('case_ref') is not None and (source_id is None or v['source_id'] == source_id)][:20]
            for value in values:
                _access(request, db, project_id, value)
            return {'items': [_report(db, session, value) for value in values]}

    @api.get('/log-sim/comparisons/{identity}', tags=['Log Sim'])
    def comparison(identity: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            value = _get(session, project_id, COMPARISON, identity)
            _access(request, db, project_id, value)
            return _report(db, session, value)

    @api.post('/log-sim/comparisons/{identity}/regressions', status_code=201, tags=['Log Sim'])
    def save_regression(identity: str, body: RegressionCreate, request: Request,
                        idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        project_authorize(request, db, body.project_id, 'Engineer')
        rid = 'logreg_'+digest({'project': body.project_id, 'key': idempotency_key})[:32]
        payload = body.model_dump() | {'comparison_id': identity}
        with LOCK, db.session.begin() as session:
            original = _get(session, body.project_id, COMPARISON, identity)
            _access(request, db, body.project_id, original, 'Engineer')
            old = log_sim._catalog(session, body.project_id, REGRESSION, rid)
            if old:
                if old['definition']['request'] != payload:
                    raise HTTPException(409, 'Idempotency key identifies another regression')
                return old['definition'] | {'runs': []}
            report = _report(db, session, original)
            if not report['result'] or report['result']['status'] != 'comparable':
                raise HTTPException(409, 'A complete comparable result is required before saving regression')
            value = dict(schema_version='log-controller-regression/1', id=rid, project_id=body.project_id,
                source_id=original['source_id'], source_kind=original['source_kind'], recording_ref=original['recording_ref'],
                name=body.name, created_at=utcnow(), request=payload, comparison_id=identity, case_ref=original['case_ref'],
                criteria={k: payload[k] for k in ('max_deceleration_request_mps2', 'max_target_speed_mps')},
                saved_output_hash=report['result']['output_hash'], saved_report_hash=report['report_hash'],
                evaluation_scope='diagnostic_open_loop_request_limits', reviewer_approval=False)
            _put(session, body.project_id, REGRESSION, rid, value)
            return value | {'runs': []}

    @api.post('/log-sim/regressions/{identity}/runs', status_code=202, tags=['Log Sim'])
    def run(identity: str, body: ProjectAction, request: Request,
            idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        project_authorize(request, db, body.project_id, 'Engineer')
        run_id = 'logrun_'+digest({'project': body.project_id, 'key': idempotency_key})[:32]
        with LOCK:
            with db.session.begin() as session:
                regression = _get(session, body.project_id, REGRESSION, identity)
                _access(request, db, body.project_id, regression, 'Engineer')
                old = log_sim._catalog(session, body.project_id, RUN, run_id)
                if old:
                    value = old['definition']
                    if value['regression_id'] != identity:
                        raise HTTPException(409, 'Idempotency key identifies another regression run')
                    pair = _get(session, body.project_id, COMPARISON, value['comparison_id'])
                else:
                    pair = copy.deepcopy(_get(session, body.project_id, COMPARISON, regression['comparison_id']))
                    pair.update(id=run_id+'_pair', execution_ids={}, created_at=utcnow(), regression_id=identity)
                    value = dict(id=run_id, project_id=body.project_id, regression_id=identity,
                        comparison_id=pair['id'], created_at=pair['created_at'])
                    _put(session, body.project_id, COMPARISON, pair['id'], pair)
                    _put(session, body.project_id, RUN, run_id, value)
            _launch(db, registry, pair)
            with db.read_session() as session:
                return _run_report(db, session, value, regression)

    @api.get('/log-sim/regressions', tags=['Log Sim'])
    def regressions(request: Request, project_id: str = 'default', source_id: str | None = None):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            values = [v for v in _values(session, project_id, REGRESSION) if source_id is None or v['source_id'] == source_id][:20]
            runs = _values(session, project_id, RUN)
            result = []
            for value in values:
                _access(request, db, project_id, value)
                result.append(value | {'runs': [_run_report(db, session, run, value) for run in runs if run['regression_id'] == value['id']]})
            return {'items': result}
