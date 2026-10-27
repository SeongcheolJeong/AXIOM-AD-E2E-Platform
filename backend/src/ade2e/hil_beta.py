"""Offline HIL authoring, explicit run association and retained observations.

The native adapter, queue and existing demo inputs remain unchanged. Catalog
rows retain immutable test revisions and explicit run links without a migration.
"""
import hashlib
import json
import math
from fastapi import Header, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from . import experiment_jobs, hil_demo, jobs
from .experiment_models import ExperimentArtifactRow, ExperimentExecutionRow
from .hil_beta_schemas import (HilDutProfileRecord, HilDutProfileSave, HilObservationCreate,
                               HilTestAction, HilTestAttach, HilTestRef, HilTestSave)
from .hil_demo_schemas import HilDemoDefinition
from .hil_scene_profiles import validate_scene_profile
from .job_models import JobRow
from .service import canonical_bytes, digest, uid, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as Row
from .team_access import project_authorize

TEST = 'hil-test'
LINK = 'hil-test-link'
OBSERVATION = 'hil-observation'
WRITE = 'hil-test-write'
PROFILE = 'hil-dut-profile'
SCHEMAS = {TEST: 'hil-sim-test/1', LINK: 'hil-sim-test-execution/1',
           OBSERVATION: 'hil-sim-observation/1', WRITE: 'hil-sim-write/1', PROFILE: 'hil-dut-profile/1',
           'hil-batch': 'hil-sim-batch/1', 'hil-batch-input': 'hil-sim-batch-input/1'}
PROFILE_PARAMETERS = [
    dict(key='max_object_age_ms', label='입력 age 임계값', type='integer', unit='ms', min=50, max=1000, step=50, default=250),
    dict(key='recovery_fresh_steps', label='회복에 필요한 연속 새 입력', type='integer', unit='steps', min=1, max=10, step=1, default=3),
]
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
EXECUTION_FIELDS = ('id', 'experiment_id', 'project_id', 'status', 'experiment_kind', 'input_hash',
                    'manifest_hash', 'adapter_id', 'adapter_version', 'fidelity', 'outcome_uncertain',
                    'created_at', 'started_at', 'finished_at', 'failure_http_status')


def _read(row):
    try:
        if hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash:
            raise ValueError('hash')
        value = json.loads(row.manifest)
        if (value['project_id'], value['id'], value['revision'], value['schema_version']) != (
                row.project_id, row.id, row.revision, SCHEMAS[row.kind]):
            raise ValueError('identity')
        if row.kind == PROFILE:
            HilDutProfileRecord.model_validate({**value, 'sha256': row.manifest_hash})
        return {**value, 'sha256': row.manifest_hash}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, 'HIL beta record failed its integrity check') from exc


def _get(session, project, kind, identity, revision=None):
    if revision is None:
        revision = session.scalar(select(func.max(Row.revision)).where(
            Row.project_id == project, Row.kind == kind, Row.id == identity))
    row = session.get(Row, (project, kind, identity, revision)) if revision else None
    if row is None:
        raise HTTPException(404, 'HIL beta record not found in project')
    return _read(row)


def _put(session, project, kind, value):
    raw = canonical_bytes(value)
    row = Row(project_id=project, kind=kind, id=value['id'], revision=value['revision'],
              manifest=raw, manifest_hash=hashlib.sha256(raw).hexdigest())
    session.add(row)
    session.flush()
    return _read(row)


def _ref(value):
    return {key: value[key] for key in ('id', 'revision', 'sha256')}


def _frozen_profiles(profiles, project):
    """Validate immutable public profile snapshots without looking up latest."""
    try:
        if not isinstance(profiles, dict) or set(profiles) != {'baseline', 'faulted'}:
            raise ValueError('sides')
        for profile in profiles.values():
            HilDutProfileRecord.model_validate(profile)
            if (profile['project_id'] != project
                    or digest({key: value for key, value in profile.items() if key != 'sha256'}) != profile['sha256']):
                raise ValueError('profile hash or project')
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(409, 'Frozen DUT profiles failed their identity, parameter or content-hash check') from error
    return profiles


def resolve_dut_profile(session, project, reference):
    reference = reference.model_dump(mode='json') if hasattr(reference, 'model_dump') else reference
    value = _get(session, project, PROFILE, reference['id'], reference['revision'])
    if value['sha256'] != reference['sha256']:
        raise HTTPException(409, 'Saved DUT profile revision hash differs')
    _frozen_profiles(dict(baseline=value, faulted=value), project)
    return value


def _execution_metadata(execution):
    value = {key: execution.get(key) for key in EXECUTION_FIELDS}
    if execution.get('comparison_kind') == 'same_fault_dut_ab':
        value.update(comparison_kind=execution['comparison_kind'], dut_profiles=execution['dut_profiles'])
    if 'scene_profile' in execution:
        value['scene_profile'] = execution['scene_profile']
    return value


def _test(session, project, ref, identity=None):
    ref = ref.model_dump(mode='json') if hasattr(ref, 'model_dump') else ref
    if identity is not None and ref['id'] != identity:
        raise HTTPException(409, 'Test URL and requested test reference differ')
    value = _get(session, project, TEST, ref['id'], ref['revision'])
    if value['sha256'] != ref['sha256']:
        raise HTTPException(409, 'Saved HIL test revision hash differs')
    return value


def _values(session, project, kind):
    latest = select(Row.id, func.max(Row.revision).label('revision')).where(
        Row.project_id == project, Row.kind == kind).group_by(Row.id).subquery()
    rows = session.scalars(select(Row).join(latest, (Row.id == latest.c.id) & (Row.revision == latest.c.revision)).where(
        Row.project_id == project, Row.kind == kind))
    return sorted((_read(row) for row in rows), key=lambda item: (item['created_at'], item['id']), reverse=True)


def _operation(session, project, scope, key, body):
    identity = 'write_' + digest([project, scope, key])[:48]
    payload = body.model_dump(mode='json')
    if isinstance(body, HilTestSave) and body.dut_profiles is None:
        payload.pop('dut_profiles')  # Preserve pre-profile request idempotency hashes.
    if isinstance(body, HilTestSave) and body.scene_profile is None:
        payload.pop('scene_profile')  # Preserve pre-scene request idempotency hashes.
    request_hash = digest(payload)
    row = session.get(Row, (project, WRITE, identity, 1))
    if row is None:
        return identity, request_hash, None
    prior = _read(row)
    if prior['request_hash'] != request_hash:
        raise HTTPException(409, 'Idempotency key already belongs to another HIL request')
    target = prior['target']
    return identity, request_hash, _get(session, project, target['kind'], target['id'], target['revision'])


def _remember(session, project, operation, request_hash, kind, value):
    _put(session, project, WRITE, dict(schema_version=SCHEMAS[WRITE], id=operation, revision=1,
        project_id=project, created_at=utcnow(), request_hash=request_hash,
        target=dict(kind=kind, id=value['id'], revision=value['revision'])))


def _binding_source(session, body):
    row = session.get(Row, (body.project_id, hil_demo.KIND, body.preset_id, body.binding_revision))
    if row is None:
        raise HTTPException(404, 'Pinned HIL preset binding revision not found')
    try:
        if hashlib.sha256(row.manifest).hexdigest() != row.manifest_hash or row.manifest_hash != body.binding_sha256:
            raise ValueError('hash')
        binding = json.loads(row.manifest)
        if (binding['project_id'], binding['kind'], binding['id'], binding['revision'], binding['schema_version']) != (
                body.project_id, hil_demo.KIND, body.preset_id, body.binding_revision, 'hil-sim-preset-binding/1'):
            raise ValueError('identity')
        source = HilDemoDefinition.model_validate(binding['definition']).model_dump(mode='json')
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, 'Pinned HIL preset binding failed its integrity check') from exc
    hil_demo._bound_source(session, body.project_id, source)
    # Representative-result replacement is not part of an executable test.
    return {key: source[key] for key in ('scenario_ref', 'seed', 'adapter_id', 'adapter_version')}


def _execution(session, project, identity, test=None):
    subject = session.get(ExperimentExecutionRow, identity)
    job = session.scalar(select(JobRow).where(JobRow.id == identity))
    if subject is None or subject.project_id != project or job is None:
        raise HTTPException(404, 'HIL execution not found in project')
    detail = experiment_jobs.detail(session, job)
    spec, snapshot = job.prepared['spec'], job.prepared['input']
    mode = spec['comparison_kind']
    if (spec['kind'] != 'hil_demo' or spec['adapter_id'] != hil_demo.ADAPTER
            or mode not in {'same_scene_fault_ab', 'same_fault_dut_ab'}
            or job.prepared['handler']['fidelity'] != hil_demo.FIDELITY
            or snapshot.get('schema_version') != 'carla-software-dut-experiment/1'
            or snapshot.get('comparison_kind') != mode):
        raise HTTPException(422, 'A native CARLA software-DUT HIL execution is required')
    profiles = None
    if mode == 'same_fault_dut_ab':
        profiles = _frozen_profiles(spec['input'].get('dut_profiles'), project)
        if canonical_bytes(snapshot.get('dut_profiles')) != canonical_bytes(profiles):
            raise HTTPException(409, 'Executed DUT profiles differ from the frozen experiment')
    elif 'dut_profiles' in spec['input'] or 'dut_profiles' in snapshot:
        raise HTTPException(409, 'Legacy fault comparison cannot contain DUT profile overrides')
    scene = None
    if 'scene_profile' in spec['input']:
        try:
            scene = validate_scene_profile(spec['input']['scene_profile'])
        except ValueError as error:
            raise HTTPException(409, 'Frozen HIL scene profile failed its integrity check') from error
    hil_demo._snapshot_scene(snapshot, scene)
    if detail.status == 'completed' and (detail.fidelity != hil_demo.FIDELITY
            or (detail.summary or {}).get('schema_version') != 'hil-demo-result/1'
            or (detail.summary or {}).get('execution_target') != 'software_dut'
            or (detail.summary or {}).get('fault') != spec['input'].get('fault')):
        raise HTTPException(409, 'Completed execution fidelity or fault differs from the software-DUT HIL contract')
    if detail.status == 'completed' and profiles is not None:
        summary = detail.summary or {}
        if (summary.get('comparison_kind') != mode or canonical_bytes(summary.get('dut_profiles')) != canonical_bytes(profiles)
                or any(canonical_bytes(summary.get('sides', {}).get(side, {}).get('dut_profile')) != canonical_bytes(profile)
                       for side, profile in profiles.items())):
            raise HTTPException(409, 'Completed DUT comparison does not retain the exact profiles on both sides')
    if detail.status == 'completed':
        summary = detail.summary or {}
        hil_demo._snapshot_scene(summary, scene)
        for side in ('baseline', 'faulted'):
            hil_demo._snapshot_scene(summary.get('sides', {}).get(side, {}), scene)
    if test:
        # Display names may change; all execution-affecting fields remain exact.
        if any(spec.get(key) != value for key, value in test['experiment'].items() if key != 'name'):
            raise HTTPException(409, 'Execution inputs differ from the saved HIL test revision')
        hil_demo._snapshot_source(snapshot, test['source'])
        if snapshot.get('fault') != test['experiment']['input']['fault']:
            raise HTTPException(409, 'Executed fault differs from the saved HIL test')
    result = detail.model_dump(mode='json')
    if profiles is not None:
        result.update(comparison_kind=mode, dut_profiles=profiles)
    if scene is not None:
        result['scene_profile'] = scene
    return result, snapshot


def _links(session, project, identity, revision=None):
    return [value for value in _values(session, project, LINK)
            if value['test_ref']['id'] == identity and (revision is None or value['test_ref']['revision'] == revision)]


def _link_result(session, project, link):
    test = _test(session, project, link['test_ref'])
    execution, _ = _execution(session, project, link['execution_id'], test)
    return {**link, 'execution': execution}


def _observations(session, project, execution_id=None, test_id=None, revision=None):
    return [value for value in _values(session, project, OBSERVATION)
            if (execution_id is None or value['execution_id'] == execution_id)
            and (test_id is None or (value.get('test_ref') or {}).get('id') == test_id)
            and (revision is None or (value.get('test_ref') or {}).get('revision') == revision)]


def _samples(db, session, execution, side, time_s):
    selected = ('baseline', 'faulted') if side == 'both' else (side,)
    if execution['status'] != 'completed' or execution['outcome_uncertain']:
        return {key: dict(status='unavailable', reason='execution_not_completed_or_uncertain') for key in selected}
    summary = execution.get('summary') or {}
    if summary.get('schema_version') != 'hil-demo-result/1' or summary.get('execution_target') != 'software_dut':
        raise HTTPException(409, 'Completed HIL execution lacks a retained software-DUT summary')
    samples = {}
    for key in selected:
        name = key + '-hil-trace.json'
        rows = list(session.scalars(select(ExperimentArtifactRow).where(
            ExperimentArtifactRow.job_id == execution['id'], ExperimentArtifactRow.name == name)))
        if len(rows) != 1 or rows[0].media_type != 'application/json' or rows[0].size_bytes > 32 * 1024 ** 2:
            raise HTTPException(409, 'Retained HIL response trace is absent or exceeds its supported size')
        artifact = rows[0]
        side_summary = summary.get('sides', {}).get(key, {})
        if side_summary.get('hil_trace_artifact') != name or side_summary.get('hil_trace_sha256') != artifact.sha256:
            raise HTTPException(409, 'HIL response trace does not match its sealed summary')
        path = experiment_jobs.artifact_path(db, artifact)
        try:
            trace = json.loads(path.read_bytes())
            frames = trace['frames']
            if (trace['schema_version'] != 'hil-demo-trace/1' or trace['side'] != key
                    or trace['execution_target'] != 'software_dut' or not isinstance(frames, list) or not frames):
                raise ValueError('trace identity')
            if execution.get('comparison_kind') == 'same_fault_dut_ab':
                profile = execution['dut_profiles'][key]
                if (trace.get('comparison_kind') != 'same_fault_dut_ab' or trace.get('fault') != summary.get('fault')
                        or canonical_bytes(trace.get('dut_profile')) != canonical_bytes(profile)):
                    raise ValueError('trace DUT profile or common fault')
            hil_demo._snapshot_scene(trace, execution.get('scene_profile'))
            previous = -1.
            sample = None
            for frame in frames:
                observed = frame['time_s']
                if type(observed) not in (int, float) or not math.isfinite(observed) or not previous < observed <= 18:
                    raise ValueError('response clock')
                previous = observed
                # Decimal UI cursors and retained tick multiplication can differ
                # by float noise; never expand this into a relative time window.
                if observed <= time_s or math.isclose(observed, time_s, rel_tol=0, abs_tol=1e-9):
                    sample = frame
            if sample is None:
                samples[key] = dict(status='unavailable', reason='before_first_retained_response', trace_sha256=artifact.sha256)
            else:
                numeric = ('input_age_ms', 'ego_speed_mps')
                if any(type(sample.get(field)) not in (int, float) or not math.isfinite(sample[field]) or sample[field] < 0 for field in numeric):
                    raise ValueError('response measurements')
                if sample.get('lead_gap_m') is not None and (type(sample['lead_gap_m']) not in (int, float) or not math.isfinite(sample['lead_gap_m'])):
                    raise ValueError('lead gap')
                if not isinstance(sample.get('dut_state'), str) or not 1 <= len(sample['dut_state']) <= 80:
                    raise ValueError('DUT state')
                command = sample.get('command')
                if not isinstance(command, dict) or set(command) != {'throttle', 'brake', 'steer'} or any(
                        type(number) not in (int, float) or not math.isfinite(number)
                        or not (-1 if field == 'steer' else 0) <= number <= 1 for field, number in command.items()):
                    raise ValueError('DUT command')
                if (type(sample.get('sequence')) is not int or sample['sequence'] < 0
                        or type(sample.get('response_frame')) is not int or sample['response_frame'] < 0):
                    raise ValueError('response identity')
                input_time = sample.get('input_time_s')
                if input_time is not None and (type(input_time) not in (int, float) or not math.isfinite(input_time)
                        or not 0 <= input_time < sample['time_s']):
                    raise ValueError('input and response clock ordering')
                samples[key] = dict(status='observed', trace_sha256=artifact.sha256,
                    **{field: sample[field] for field in ('sequence', 'response_frame', 'time_s')},
                    input_time_s=sample.get('input_time_s'),
                    **{field: sample.get(field) for field in ('input_age_ms', 'dut_state', 'ego_speed_mps', 'lead_gap_m', 'command')})
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, 'Retained HIL response trace is invalid') from exc
    return samples


def register_hil_beta_routes(api, db, registry):
    @api.get('/hil-sim/dut-profiles', tags=['HIL beta'])
    def profiles(request: Request, project_id: str = 'default', limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            values = _values(session, project_id, PROFILE)
            return dict(schema_version='hil-dut-profiles/1', items=values[offset:offset + limit],
                next_offset=offset + limit if len(values) > offset + limit else None,
                parameter_definitions=PROFILE_PARAMETERS)

    @api.post('/hil-sim/dut-profiles', status_code=201, tags=['HIL beta'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def save_profile(body: HilDutProfileSave, request: Request,
                     idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, request_hash, prior = _operation(session, body.project_id, 'profile-save', idempotency_key, body)
            if prior:
                return prior
            previous = _get(session, body.project_id, PROFILE, body.id) if body.id else None
            current = previous['revision'] if previous else 0
            if current != body.expected_revision:
                raise HTTPException(409, 'DUT profile revision changed; reload before saving')
            value = dict(schema_version=SCHEMAS[PROFILE], id=body.id or uid('hildut'), project_id=body.project_id,
                revision=current + 1, name=body.name, note=body.note, parameters=body.parameters.model_dump(mode='json'),
                created_at=utcnow(), author=dict(user_label=principal.user_label, role=principal.role))
            result = _put(session, body.project_id, PROFILE, value)
            _remember(session, body.project_id, operation, request_hash, PROFILE, result)
            return result

    @api.get('/hil-sim/dut-profiles/{identity}', tags=['HIL beta'])
    def get_profile(identity: str, request: Request, project_id: str = 'default', revision: int | None = Query(None, ge=1)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return _get(session, project_id, PROFILE, identity, revision)

    @api.get('/hil-sim/tests', tags=['HIL beta'])
    def list_tests(request: Request, project_id: str = 'default', limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            values = _values(session, project_id, TEST)
            return dict(schema_version='hil-sim-tests/1', items=values[offset:offset + limit],
                        next_offset=offset + limit if len(values) > offset + limit else None)

    @api.post('/hil-sim/tests', status_code=201, tags=['HIL beta'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def save_test(body: HilTestSave, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, request_hash, prior_result = _operation(session, body.project_id, 'save', idempotency_key, body)
            if prior_result:
                return prior_result
            previous = _get(session, body.project_id, TEST, body.id) if body.id else None
            current = previous['revision'] if previous else 0
            if current != body.expected_revision:
                raise HTTPException(409, 'HIL test revision changed; reload before saving')
            source = _binding_source(session, body)
            parameters = hil_demo._parameters(body.preset_id, body.parameters)
            copied = body.copied_from.model_dump(mode='json') if body.copied_from else (previous or {}).get('copied_from')
            if copied:
                original = _test(session, body.project_id, copied)
                if original['source'] != source or original['preset_id'] != body.preset_id:
                    raise HTTPException(409, 'Duplicated test must preserve its original scenario and preset source')
            profiles = ({side: resolve_dut_profile(session, body.project_id, reference)
                         for side, reference in body.dut_profiles.model_dump(mode='json').items()}
                        if body.dut_profiles is not None else None)
            scene_profile = hil_demo._resolve_scene(body.scene_profile)
            definition = hil_demo._definition(body.preset_id, body.project_id, source, parameters, profiles,
                                             scene_profile).model_copy(update={'name': body.name})
            value = dict(schema_version=SCHEMAS[TEST], id=body.id or uid('hiltest'), project_id=body.project_id,
                revision=current + 1, name=body.name, note=body.note, preset_id=body.preset_id,
                binding_ref=dict(id=body.preset_id, revision=body.binding_revision, sha256=body.binding_sha256),
                parameters=parameters, source=source, experiment=definition.model_dump(mode='json'), copied_from=copied,
                created_at=utcnow(), author=dict(user_label=principal.user_label, role=principal.role))
            if profiles is not None:
                value['dut_profiles'] = profiles
            if scene_profile is not None:
                value['scene_profile'] = scene_profile
            result = _put(session, body.project_id, TEST, value)
            _remember(session, body.project_id, operation, request_hash, TEST, result)
            return result

    @api.get('/hil-sim/tests/{identity}', tags=['HIL beta'])
    def get_test(identity: str, request: Request, project_id: str = 'default', revision: int | None = Query(None, ge=1)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return _get(session, project_id, TEST, identity, revision)

    @api.post('/hil-sim/tests/{identity}/preview', tags=['HIL beta'],
              openapi_extra={'x-axiom-min-role': 'Engineer', 'x-axiom-read-only': True})
    def preview_test(identity: str, body: HilTestAction, request: Request):
        project_authorize(request, db, body.project_id, 'Engineer')
        with db.read_session() as session:
            test = _test(session, body.project_id, body.test_ref, identity)
            hil_demo._bound_source(session, body.project_id, test['source'])
        from .experiment_schemas import ExperimentCreate
        definition = ExperimentCreate.model_validate(test['experiment'])
        prepared = experiment_jobs.prepare(registry, definition)
        payload = prepared.to_payload()
        hil_demo._snapshot_source(payload['input'], test['source'])
        hil_demo._snapshot_scene(payload['input'], test.get('scene_profile'))
        if 'dut_profiles' in test and canonical_bytes(payload['input'].get('dut_profiles')) != canonical_bytes(test['dut_profiles']):
            raise HTTPException(409, 'Prepared DUT profiles differ from the saved HIL test revision')
        return dict(schema_version='hil-sim-test-preview/1', test_ref=_ref(test), project_id=body.project_id,
            preset_id=test['preset_id'], parameters=test['parameters'], experiment=test['experiment'],
            **({'dut_profiles': test['dut_profiles']} if 'dut_profiles' in test else {}),
            **({'scene_profile': test['scene_profile']} if 'scene_profile' in test else {}),
            changes=hil_demo._changes(test['preset_id'], test['parameters']),
            preview=dict(preview_hash=prepared.input_hash, prepared=payload, job_count=1,
                resource_id=payload['handler']['resource_id'], execution_fidelity=payload['input'].get('execution_fidelity', payload['handler']['fidelity']),
                requires_explicit_submit=True, evaluation_status='not_evaluated'))

    @api.post('/hil-sim/tests/{identity}/executions', status_code=201, tags=['HIL beta'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def attach(identity: str, body: HilTestAttach, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, request_hash, prior = _operation(session, body.project_id, 'attach:' + identity, idempotency_key, body)
            if prior:
                return _link_result(session, body.project_id, prior)
            test = _test(session, body.project_id, body.test_ref, identity)
            execution, _ = _execution(session, body.project_id, body.execution_id, test)
            link_id = 'hilrun_' + digest([identity, test['revision'], execution['id']])[:48]
            row = session.get(Row, (body.project_id, LINK, link_id, 1))
            link = _read(row) if row else _put(session, body.project_id, LINK, dict(schema_version=SCHEMAS[LINK],
                id=link_id, revision=1, project_id=body.project_id, test_ref=_ref(test), execution_id=execution['id'],
                input_hash=execution['input_hash'], created_at=utcnow(), author=dict(user_label=principal.user_label, role=principal.role)))
            _remember(session, body.project_id, operation, request_hash, LINK, link)
            return {**link, 'execution': execution}

    @api.get('/hil-sim/tests/{identity}/executions', tags=['HIL beta'])
    def executions(identity: str, request: Request, project_id: str = 'default', revision: int | None = Query(None, ge=1),
                   limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            _get(session, project_id, TEST, identity, revision)
            links = _links(session, project_id, identity, revision)
            return dict(schema_version='hil-sim-test-executions/1', items=[_link_result(session, project_id, link) for link in links[offset:offset + limit]],
                        next_offset=offset + limit if len(links) > offset + limit else None)

    @api.post('/hil-sim/observations', status_code=201, tags=['HIL beta'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def create_observation(body: HilObservationCreate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, request_hash, prior = _operation(session, body.project_id, 'observe', idempotency_key, body)
            if prior:
                return prior
            test = _test(session, body.project_id, body.test_ref) if body.test_ref else None
            if test and not any(link['execution_id'] == body.execution_id for link in _links(session, body.project_id, test['id'], test['revision'])):
                raise HTTPException(409, 'Attach this execution to the exact test revision before saving its observation')
            execution, snapshot = _execution(session, body.project_id, body.execution_id, test)
            if execution['status'] not in TERMINAL:
                raise HTTPException(422, 'Wait for a terminal HIL execution before recording its observation')
            duration = snapshot.get('city_snapshot', {}).get('scenario', {}).get('duration_s', 18)
            if body.time_s > duration:
                raise HTTPException(422, 'Observation time exceeds the executed scenario')
            value = dict(schema_version=SCHEMAS[OBSERVATION], id=uid('hilnote'), revision=1,
                **body.model_dump(mode='json'), created_at=utcnow(), author=dict(user_label=principal.user_label, role=principal.role),
                execution=_execution_metadata(execution),
                samples=_samples(db, session, execution, body.side, body.time_s),
                assessment_source='user_observation', time_basis='retained_response_clock_user_selection',
                verification_status='not_a_test_verdict')
            result = _put(session, body.project_id, OBSERVATION, value)
            _remember(session, body.project_id, operation, request_hash, OBSERVATION, result)
            return result

    @api.get('/hil-sim/observations', tags=['HIL beta'])
    def observations(request: Request, project_id: str = 'default', execution_id: str | None = None,
                     test_id: str | None = None, revision: int | None = Query(None, ge=1),
                     limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            if execution_id:
                _execution(session, project_id, execution_id)
            if test_id:
                _get(session, project_id, TEST, test_id, revision)
            elif revision is not None:
                raise HTTPException(422, 'Observation revision filtering requires test_id')
            values = _observations(session, project_id, execution_id, test_id, revision)
            return dict(schema_version='hil-sim-observations/1', items=values[offset:offset + limit],
                next_offset=offset + limit if len(values) > offset + limit else None, verification_status='not_a_test_verdict')

    @api.get('/hil-sim/tests/{identity}/export', tags=['HIL beta'])
    def export(identity: str, request: Request, project_id: str = 'default', revision: int | None = Query(None, ge=1)):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            test = _get(session, project_id, TEST, identity, revision)
            links = [_link_result(session, project_id, link) for link in _links(session, project_id, identity, test['revision'])]
            execution_ids = {link['execution_id'] for link in links}
            notes = [value for value in _observations(session, project_id)
                if value['execution_id'] in execution_ids and (value.get('test_ref') is None or value['test_ref'] == _ref(test))]
            # Export explicit public metadata only, not native prepared transport
            # paths, diagnostic error bodies, connection settings or credentials.
            rows = [dict(test_ref=link['test_ref'], execution=_execution_metadata(link['execution']),
                artifacts=[{key: item[key] for key in ('id', 'name', 'sha256', 'size_bytes', 'media_type')}
                           for item in link['execution']['artifacts']]) for link in links]
            value = dict(schema_version='hil-sim-test-export/1', test=test, executions=rows, observations=notes,
                exported_at=utcnow(), execution_target='software_dut', verification_status='not_a_test_verdict')
            raw = canonical_bytes(value)
            return Response(raw, media_type='application/json', headers={'X-Content-SHA256': hashlib.sha256(raw).hexdigest(),
                'Content-Disposition': f'attachment; filename="{test["id"]}-r{test["revision"]}.json"'})
