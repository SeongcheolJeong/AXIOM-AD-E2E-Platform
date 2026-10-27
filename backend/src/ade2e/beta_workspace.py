"""Project beta workspace over retained domain records; never dispatches engines.

Common tasks store links, not copies of execution state. Log guided tasks and
feedback are projected from their original catalog. Registration performs no
engine readiness probes, migrations, source imports or service changes.
"""
import csv
import io
import math
from typing import Literal
from urllib.parse import parse_qsl, urlencode

from fastapi import Header, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import select

from . import (experiment_jobs, hil_beta, jobs, log_sim, log_sim_beta_tasks as log_tasks,
               log_sim_replays as catalog, object_beta_observations, object_demo)
from .beta_workspace_schemas import FeedbackCreate, FeedbackTriage, Module, TaskCreate, TaskProgress
from .experiment_models import ExperimentExecutionRow
from .hil_beta_schemas import HilTestRef
from .job_models import JobRow
from .log_sim_beta_packs import PACKS
from .object_demo_schemas import DemoScenarioReference
from .scenario_v2 import get_scenario
from .scenario_v2_models import ScenarioV2Row, ScenarioV2RevisionRow
from .service import digest, utcnow
from .simulation_catalog_models import SimulationCatalogRevisionRow as CatalogRow
from .team_access import project_authorize
from .team_models import ProjectRow, ProjectTokenRow

TASK = 'beta-task'
FEEDBACK = 'beta-feedback'
WRITE = 'beta-write'
LOG_TRIAGE = 'beta-log-triage'
MODULES = {'object': ('Object Sim', 'object_sim'), 'log': ('Log Sim', 'planning_replay'),
           'hil': ('Software-DUT HIL', 'hil_demo'), 'sensor': ('Sensor Sim', 'sensor_capture'),
           'neural': ('Neural Sim', 'neural_reconstruction')}
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


def _operator(request):
    principal = getattr(request.state, 'principal', None)
    if principal is None:
        raise HTTPException(401, 'Authenticated operator context is required')
    if principal.mode == 'project':
        raise HTTPException(403, 'Only the operator can manage beta participants and triage')
    return principal


def _participant(principal):
    return dict(participant=principal.user_label, participant_mode=principal.mode,
                participant_id=digest(['beta-participant/1', principal.token_id or principal.mode]))


def _operation(session, project, scope, key, body):
    operation = 'write_' + digest([project, scope, key])[:48]
    payload = body.model_dump(mode='json')
    # New optional Sensor fields must not change keys for prior beta writes.
    for field in ('guide_id', 'sensor_key'):
        if payload.get(field) is None:
            payload.pop(field, None)
    previous = log_sim._catalog(session, project, WRITE, operation)
    if previous:
        if previous['definition']['request'] != payload:
            raise HTTPException(409, 'Idempotency key belongs to a different beta request')
        return operation, payload, previous['definition']['result']
    return operation, payload, None


def _remember(session, project, operation, payload, result):
    catalog._put(session, project, WRITE, operation, dict(request=payload, result=result))


def _source(session, project, module, ref):
    if module == 'sensor':
        from . import sensor_layouts
        from .sensor_layout_schemas import SensorLayoutRef
        try:
            parsed = SensorLayoutRef.model_validate(ref)
        except ValidationError as exc:
            raise HTTPException(422, 'Select an exact saved layout revision and its content hash') from exc
        value = sensor_layouts._layout(session, project, parsed.model_dump(mode='json'))
        return value, _sensor_duration(value)
    try:
        parsed = (DemoScenarioReference if module == 'object' else HilTestRef).model_validate(ref)
    except ValidationError as exc:
        raise HTTPException(422, 'Select an exact saved revision with its content hashes') from exc
    ref = parsed.model_dump(mode='json')
    if module == 'hil':
        value = hil_beta._test(session, project, ref)
        return value, 18.0
    value = get_scenario(session, ref['id'], ref['revision'])
    if value['project_id'] != project:
        raise HTTPException(404, 'Saved scenario not found in project')
    if value['scenario']['schema_version'] != 'scenario-v2/3':
        raise HTTPException(422, 'Object beta requires a native city scenario revision')
    if any(value[key] != ref[key] for key in ('configuration_hash', 'manifest_hash')):
        raise HTTPException(409, 'Scenario content hashes do not match its exact revision')
    return value, float(value['scenario']['duration_s'])


def _sensor_duration(value):
    """Retained timestamps start at zero: 20 s at 10 Hz ends at 19.9 s."""
    rigs = value.get('rigs', value)
    durations = []
    for side in ('rig_a', 'rig_b'):
        rig = rigs.get(side, {})
        seconds, hz = rig.get('duration_seconds'), rig.get('sample_hz')
        if (type(seconds) not in (int, float) or type(hz) not in (int, float)
                or not math.isfinite(seconds) or not math.isfinite(hz) or seconds <= 0 or hz <= 0):
            return None
        durations.append((seconds * hz - 1) / hz)
    return min(durations)


def _execution(session, project, module, identity, source=None):
    if module == 'object':
        value, snapshot = object_demo._execution(session, project, identity)
        return value['execution'], value['scenario_ref'], float(value['scenario']['duration_s'])
    if module == 'hil':
        value, snapshot = hil_beta._execution(session, project, identity, source)
        duration = snapshot.get('city_snapshot', {}).get('scenario', {}).get('duration_s', 18)
        return value, hil_beta._ref(source) if source else None, float(duration)
    if module == 'sensor' and source is not None:
        from . import sensor_layouts
        ref = sensor_layouts._ref(source)
        linked = [link for link in sensor_layouts._values(session, project, sensor_layouts.LINK)
                  if link['layout_ref'] == ref and link['execution_id'] == identity]
        if not linked:
            raise HTTPException(409, 'Link the execution to this exact saved Sensor layout revision first')
        value, snapshot = sensor_layouts._execution(session, project, identity, source)
        if any(link['input_hash'] != value['input_hash'] for link in linked):
            raise HTTPException(409, 'Sensor layout link and execution input hashes differ')
        return value, ref, _sensor_duration(snapshot)
    subject = session.get(ExperimentExecutionRow, identity)
    job = session.scalar(select(JobRow).where(JobRow.id == identity))
    if subject is None or subject.project_id != project or job is None:
        raise HTTPException(404, 'Execution not found in project')
    kind = job.prepared.get('spec', {}).get('kind')
    allowed = {'sensor': {'sensor_capture'}, 'neural': {'neural_reconstruction', 'neural_render', 'neural_perception'}}
    if kind not in allowed.get(module, set()):
        raise HTTPException(422, 'Execution belongs to a different module')
    value = experiment_jobs.detail(session, job).model_dump(mode='json')
    snapshot = job.prepared.get('input', {})
    duration = snapshot.get('duration_s')
    if module == 'sensor':
        duration = _sensor_duration(snapshot)
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
        duration = None
    return value, None, duration


def _resume(value):
    module, project = value['module'], value['project_id']
    query = dict(project=project)
    if module == 'log':
        query.update(work='guided', task=value['id'])
        if value.get('source_id'):
            query['source'] = value['source_id']
    else:
        if value.get('id'):
            query['beta_task'] = value['id']
        ref = value.get('source_ref')
        execution = value.get('execution_id')
        if module == 'object':
            if execution:
                query.update(mode='story', left=execution, origin='beta')
            elif ref:
                query.update(mode='city', scenario=ref['id'], revision=ref['revision'])
            else:
                query.update(mode='beta', origin='beta')
        elif module == 'hil':
            query['mode'] = 'beta'
            if ref:
                query.update(test=ref['id'], revision=ref['revision'])
            if execution:
                query['result'] = execution
        elif module == 'sensor':
            layout_mode = bool(ref or value.get('id') or value.get('guide_id'))
            if layout_mode:
                query['mode'] = 'layout'
                if value.get('guide_id'):
                    query['guide'] = value['guide_id']
                if ref:
                    query.update(layout=ref['id'], revision=ref['revision'])
            if execution:
                query['result' if layout_mode else 'execution'] = execution
            if value.get('sensor_key'):
                query['sensor'] = value['sensor_key']
            if value.get('observation_id'):
                query['observation'] = value['observation_id']
        elif execution:
            query['execution'] = execution
    if value.get('time_s') is not None:
        query['time_s'] = value['time_s']
    return '#' + module + '-sim?' + urlencode(query)


def _present(session, value):
    value = dict(value)
    source = duration = execution = None
    if value['source_ref']:
        source, duration = _source(session, value['project_id'], value['module'], value['source_ref'])
    if value.get('sensor_key') and value['module'] != 'sensor':
        raise HTTPException(422, 'Sensor selection belongs to Sensor Sim tasks')
    if value['execution_id']:
        if source is None:
            raise HTTPException(409, 'An execution must retain its saved source revision')
        execution, ref, duration = _execution(session, value['project_id'], value['module'], value['execution_id'], source)
        if ref != value['source_ref']:
            raise HTTPException(409, 'Execution differs from the task saved revision')
        if value['mode'] == 'new_execution' and execution['created_at'] < value['created_at']:
            raise HTTPException(409, 'A retained execution cannot complete a new-execution task; create a retained-playback task')
    if value['time_s'] is not None and (not execution or duration is None or value['time_s'] > duration):
        raise HTTPException(422, 'Playback time requires a linked execution and must be within its duration')
    if value['result_opened'] and (not execution or execution['status'] not in TERMINAL):
        raise HTTPException(409, 'A result can be marked viewed only after execution is terminal')
    if value['observation_id']:
        if not execution:
            raise HTTPException(422, 'Link the execution before its observation')
        if value['module'] == 'hil':
            observation = hil_beta._get(session, value['project_id'], hil_beta.OBSERVATION, value['observation_id'])
            if observation.get('test_ref') != value['source_ref']:
                raise HTTPException(409, 'Observation does not reference this exact HIL test revision')
        elif value['module'] == 'sensor':
            from . import sensor_layouts
            observation = sensor_layouts._get(session, value['project_id'], sensor_layouts.OBSERVATION, value['observation_id'])
            if observation['layout_ref'] != value['source_ref']:
                raise HTTPException(409, 'Observation does not reference this exact Sensor layout revision')
            if value.get('sensor_key') is not None and value['sensor_key'] != observation['sensor_key']:
                raise HTTPException(409, 'Observation belongs to a different selected sensor')
            if value['time_s'] is not None and value['time_s'] != observation['time_s']:
                raise HTTPException(409, 'Observation timestamp differs from the task playback timestamp')
            moment = observation['time_s']
            if (type(moment) not in (int, float) or not math.isfinite(moment)
                    or duration is None or not 0 <= moment <= duration):
                raise HTTPException(422, 'Sensor observation is outside its retained execution duration')
            value.update(sensor_key=observation['sensor_key'], time_s=moment)
        else:
            row = session.get(CatalogRow, (value['project_id'], object_beta_observations.KIND, value['observation_id'], 1))
            if row is None:
                raise HTTPException(404, 'Observation not found in project')
            observation = object_beta_observations._read(row)
            if observation['scenario_ref'] != value['source_ref']:
                raise HTTPException(409, 'Observation does not reference this exact scenario revision')
        if observation['execution_id'] != value['execution_id']:
            raise HTTPException(409, 'Observation belongs to a different execution')
    completed_run = bool(execution and execution['status'] == 'completed' and not execution.get('outcome_uncertain'))
    steps = [dict(id='authoring', label='저장 개정 연결', complete=bool(source), basis='saved_revision'),
             dict(id='execution', label='새 실행 완료' if value['mode'] == 'new_execution' else '저장 실행 연결', complete=completed_run, basis='execution_record'),
             dict(id='investigation', label='결과 확인', complete=value['result_opened'], basis='participant_report'),
             dict(id='observation', label='관찰 저장', complete=bool(value['observation_id']), basis='saved_observation')]
    completed = all(step['complete'] for step in steps)
    blocked = bool(execution and execution['status'] in {'failed', 'cancelled', 'interrupted'})
    value.update(steps=steps, origin='common', status='completed' if completed else 'blocked' if blocked else 'active',
        stage=next((s['id'] for s in steps if not s['complete']), 'completed'),
        execution_status=execution['status'] if execution else None, duration_s=duration,
        completion_basis=('new_execution_observation' if value['mode'] == 'new_execution' else 'retained_playback_observation') if completed else None,
        execution={key: execution.get(key) for key in ('id', 'status', 'adapter_id', 'adapter_version', 'fidelity', 'input_hash', 'manifest_hash', 'created_at')} if execution else None)
    value['resume_url'] = _resume(value)
    return value


def _log_present(db, request, session, value):
    participant_token = next((token for token in session.scalars(select(ProjectTokenRow.id).where(
        ProjectTokenRow.project_id == value['project_id'])) if digest(['log-beta-participant/1', token]) == value['participant_id']), None)
    participant_mode = 'project' if participant_token else 'operator_or_unknown'
    participant_id = digest(['beta-participant/1', participant_token]) if participant_token else value['participant_id']
    try:
        current = log_tasks._present(db, request, session, value)
    except HTTPException as exc:
        current = dict(value, status='blocked', stage='source', execution_status=None, execution_reason=str(exc.detail))
    titles = {p['id']: p['title'] for p in PACKS}
    stage_order = ['source', 'settings', 'execution', 'investigation', 'review', 'completed']
    index = stage_order.index(current['stage'])
    result = dict(current, module='log', title=titles.get(value['pack_id'], value['pack_id']), mode='new_execution',
        origin='log_guided', participant_mode=participant_mode, participant_id=participant_id, source_ref=current.get('comparison_ref'), execution_id=None, observation_id=None,
        time_s=None, steps=[dict(id=s, label=label, complete=i < index, basis='domain_record' if s != 'investigation' else 'participant_report')
            for i, (s, label) in enumerate(zip(stage_order[:-1], ['입력 선택', '조건 저장', '비교 실행', '결과 확인', 'Case 검토']))],
        completion_basis='log_guided_review' if current['status'] == 'completed' else None)
    result['resume_url'] = _resume(result)
    return result


def _task(db, request, session, project, identity):
    if identity.startswith('logtask_'):
        return _log_present(db, request, session, catalog._get(session, project, log_tasks.TASK, identity))
    return _present(session, catalog._get(session, project, TASK, identity))


def _tasks(db, request, session, project):
    result = []
    for value in catalog._values(session, project, TASK):
        try:
            result.append(_present(session, value))
        except HTTPException as exc:
            result.append(dict(value, status='blocked', stage='authoring', steps=[], origin='common',
                resume_url='#workspace?' + urlencode(dict(project=project, beta_task=value['id'])),
                execution_status=None, execution_reason=str(exc.detail), completion_basis=None))
    result.extend(_log_present(db, request, session, item) for item in catalog._values(session, project, log_tasks.TASK))
    return sorted(result, key=lambda item: item['updated_at'], reverse=True)


def _feedback(session, project):
    result = catalog._values(session, project, FEEDBACK)
    for item in catalog._values(session, project, log_tasks.FEEDBACK):
        projected = dict(item, module='log', origin='log_guided', revision=1, updated_at=item['created_at'],
            status='received', kind='help' if item['kind'] == 'blocked' else 'completion', triage_note=None,
            context={key: item.get(key) for key in ('task_revision', 'source_id', 'source_hash', 'comparison_ref', 'batch_id', 'case_id')})
        projected['context']['resume_url'] = _resume(dict(item, module='log', id=item['task_id']))
        projected['stage'] = item.get('task_stage', item.get('stage'))
        overlay = log_sim._catalog(session, project, LOG_TRIAGE, item['id'])
        if overlay:
            projected.update(overlay['definition'])
        result.append(projected)
    return sorted(result, key=lambda item: item['updated_at'], reverse=True)


def _evidence(db, request, session, project, module):
    items = []
    if module == 'log':
        for pack in PACKS:
            binding = log_sim._catalog(session, project, 'log-beta-pack', pack['id'])
            if binding or pack['id'] == 'input-errors':
                items.append(dict(id=pack['id'], kind='pack', label=pack['title'], status='available'))
        return items
    if module == 'object':
        rows = session.execute(select(ScenarioV2Row.id, ScenarioV2RevisionRow.revision).join(
            ScenarioV2RevisionRow, ScenarioV2RevisionRow.scenario_id == ScenarioV2Row.id).where(
            ScenarioV2Row.project_id == project).order_by(ScenarioV2Row.id, ScenarioV2RevisionRow.revision.desc()))
        for identity, revision in rows:
            value = get_scenario(session, identity, revision)
            if value['scenario']['schema_version'] != 'scenario-v2/3':
                continue
            ref = {key: value[key] for key in ('id', 'revision', 'configuration_hash', 'manifest_hash')}
            items.append(dict(id=identity + ':' + str(revision), kind='saved_revision',
                label=f"{value['scenario']['name']} · r{revision}", source_ref=ref, duration_s=value['scenario']['duration_s']))
    elif module == 'sensor':
        from . import sensor_layouts
        rows = session.scalars(select(CatalogRow).where(CatalogRow.project_id == project,
            CatalogRow.kind == sensor_layouts.LAYOUT).order_by(CatalogRow.id, CatalogRow.revision.desc()))
        for row in rows:
            value = sensor_layouts._get(session, project, sensor_layouts.LAYOUT, row.id, row.revision)
            items.append(dict(id=row.id + ':' + str(row.revision), kind='saved_revision',
                label=f"{value['name']} · r{row.revision}", source_ref=sensor_layouts._ref(value),
                duration_s=_sensor_duration(value)))
    else:
        rows = session.scalars(select(CatalogRow).where(CatalogRow.project_id == project, CatalogRow.kind == hil_beta.TEST).order_by(CatalogRow.id, CatalogRow.revision.desc()))
        for row in rows:
            value = hil_beta._read(row)
            items.append(dict(id=row.id + ':' + str(row.revision), kind='saved_revision',
                label=f"{value['name']} · r{row.revision}", source_ref=hil_beta._ref(value), duration_s=18))
    sources = [item for item in items if item['kind'] == 'saved_revision']
    executions = session.scalars(select(ExperimentExecutionRow).where(ExperimentExecutionRow.project_id == project))
    for row in executions:
        job = session.scalar(select(JobRow).where(JobRow.id == row.job_id))
        if not job or job.prepared.get('spec', {}).get('kind') != MODULES[module][1]:
            continue
        try:
            if module == 'object':
                execution, ref, duration = _execution(session, project, module, row.job_id)
                items.append(dict(id=row.job_id, kind='execution', label=f"{row.job_id} · {execution['status']}",
                    execution_id=row.job_id, source_ref=ref, duration_s=duration, status=execution['status'], created_at=execution['created_at']))
            else:
                for source in sources:
                    try:
                        saved, _ = _source(session, project, module, source['source_ref'])
                        execution, ref, duration = _execution(session, project, module, row.job_id, saved)
                        items.append(dict(id=row.job_id + ':' + source['id'], kind='execution', label=f"{source['label']} · {execution['status']}",
                            execution_id=row.job_id, source_ref=ref, duration_s=duration, status=execution['status'], created_at=execution['created_at']))
                    except HTTPException:
                        continue
        except HTTPException:
            continue
    if module == 'sensor':
        observations = sensor_layouts._values(session, project, sensor_layouts.OBSERVATION)
    elif module == 'hil':
        observations = hil_beta._values(session, project, hil_beta.OBSERVATION)
    else:
        observations = [object_beta_observations._read(row) for row in session.scalars(select(CatalogRow).where(
            CatalogRow.project_id == project, CatalogRow.kind == object_beta_observations.KIND))]
    for value in observations:
        items.append(dict(id=value['id'], kind='observation', label=value['title'], observation_id=value['id'],
            execution_id=value['execution_id'], source_ref=value['layout_ref'] if module == 'sensor' else value.get('test_ref') if module == 'hil' else value['scenario_ref'],
            **(dict(sensor_key=value['sensor_key'], time_s=value['time_s']) if module == 'sensor' else {})))
    return items


def register_beta_workspace_routes(api, db, registry):
    @api.get('/beta/readiness', tags=['Beta workspace'])
    def readiness(request: Request, project_id: str = 'default'):
        principal = project_authorize(request, db, project_id)
        writable = principal.mode != 'project' or principal.role in {'Engineer', 'Admin'}
        handlers = registry.list()
        items = []
        with db.read_session() as session:
            retained_kinds = set()
            for row, job in session.execute(select(ExperimentExecutionRow, JobRow).join(JobRow, JobRow.id == ExperimentExecutionRow.job_id).where(
                    ExperimentExecutionRow.project_id == project_id, JobRow.status == 'completed')):
                retained_kinds.add(job.prepared.get('spec', {}).get('kind'))
            from .object_beta_access import policy_status
            policy = policy_status(session, project_id)
            for module, (title, kind) in MODULES.items():
                configured = any(kind in handler.kinds and handler.status == 'available' for handler in handlers)
                retained = kind in retained_kinds or module == 'neural' and 'neural_render' in retained_kinds
                reason = ('프로젝트에 실행 권한이 없습니다.' if not writable else
                          '이 서비스에 사용 가능한 실행 어댑터가 설정되지 않았습니다.' if not configured else
                          '어댑터 설정만 확인했습니다. Worker 연결·공유 실행 자원·저장 공간은 운영자 확인이 필요합니다.')
                policy_blocked = policy['enabled'] and (module != 'object' or not policy['can_submit'] or not any(
                    kind in handler.kinds and handler.resource_id == policy['resource_id'] for handler in handlers))
                if policy_blocked:
                    reason = ('이 프로젝트는 지정된 Object Sim 자원만 실행하도록 설정되어 있습니다.' if module != 'object' else
                              '프로젝트 실행 대기 한도 또는 지정 자원 설정으로 새 제출이 제한됩니다.')
                items.append(dict(module=module, title=title, guided=module in {'object', 'log', 'hil', 'sensor'},
                    authoring=dict(status='available' if writable else 'blocked', reason='프로젝트 저장 권한 확인' if writable else 'Engineer 권한 필요'),
                    retained_playback=dict(status='available' if retained else 'empty', reason='이 프로젝트의 완료 실행 기록 있음' if retained else '이 서비스의 프로젝트에 완료 실행 없음'),
                    new_execution=dict(status='unknown' if writable and configured and not policy_blocked else 'blocked', reason=reason, configured=configured, worker_verified=False),
                    entry_url='#' + module + '-sim?' + urlencode(dict(project=project_id, **({'mode': 'layout'} if module == 'sensor' else {})))))
        return dict(schema_version='beta-readiness/1', project_id=project_id, checked_at=utcnow(), items=items)

    @api.get('/beta/evidence', tags=['Beta workspace'])
    def evidence(request: Request, module: Literal['object', 'log', 'hil', 'sensor'], project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return dict(schema_version='beta-evidence/1', project_id=project_id, module=module,
                        items=_evidence(db, request, session, project_id, module))

    @api.get('/beta/tasks', tags=['Beta workspace'])
    def list_tasks(request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return dict(schema_version='beta-tasks/1', items=_tasks(db, request, session, project_id))

    @api.get('/beta/tasks/{identity}', tags=['Beta workspace'])
    def get_task(identity: str, request: Request, project_id: str = 'default'):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            return _task(db, request, session, project_id, identity)

    @api.post('/beta/tasks', status_code=201, tags=['Beta workspace'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def create_task(body: TaskCreate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, payload, previous = _operation(session, body.project_id, 'create', idempotency_key, body)
            if previous:
                return previous
            if body.guide_id is not None and body.module != 'sensor':
                raise HTTPException(422, 'Sensor guides belong to Sensor Sim tasks')
            now = utcnow()
            if body.module == 'log':
                if body.mode != 'new_execution' or body.pack_id not in {p['id'] for p in PACKS}:
                    raise HTTPException(422, 'Log guided tasks require new_execution and an available pack_id')
                identity = 'logtask_' + digest([body.project_id, 'common-beta', idempotency_key])[:32]
                source_id = source_hash = None
                if body.pack_id != 'input-errors':
                    binding = log_sim._catalog(session, body.project_id, 'log-beta-pack', body.pack_id)
                    if not binding:
                        raise HTTPException(409, 'Ask the operator to connect this Log pack source')
                    bound = binding['definition']
                    source = log_tasks._source(db, request, session, body.project_id, bound['source_id'], bound['source_hash'])
                    source_id, source_hash = source['id'], source['source_hash']
                value = dict(schema_version='log-beta-task/1', id=identity, project_id=body.project_id, pack_id=body.pack_id,
                    revision=1, source_id=source_id, source_hash=source_hash, comparison_ref=None, batch_id=None, case_id=None,
                    result_opened=False, created_at=now, updated_at=now, participant=principal.user_label,
                    participant_id=log_tasks._principal_id(principal))
                catalog._put(session, body.project_id, log_tasks.TASK, identity, value)
                result = _log_present(db, request, session, value)
            else:
                if body.pack_id is not None:
                    raise HTTPException(422, 'pack_id belongs to Log guided tasks')
                identity = 'betatask_' + digest([body.project_id, idempotency_key])[:32]
                value = dict(schema_version='beta-task/1', id=identity, revision=1, project_id=body.project_id,
                    module=body.module, title=body.title, mode=body.mode, source_ref=None, execution_id=None,
                    observation_id=None, result_opened=False, time_s=None, created_at=now, updated_at=now, **_participant(principal))
                if body.module == 'sensor':
                    value.update(guide_id=body.guide_id or 'camera_direction', sensor_key=None)
                catalog._put(session, body.project_id, TASK, identity, value)
                result = _present(session, value)
            _remember(session, body.project_id, operation, payload, result)
            return result

    @api.post('/beta/tasks/{identity}/progress', tags=['Beta workspace'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def progress(identity: str, body: TaskProgress, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, payload, previous = _operation(session, body.project_id, 'progress:' + identity, idempotency_key, body)
            if previous:
                return previous
            if identity.startswith('logtask_'):
                catalog._get(session, body.project_id, log_tasks.TASK, identity)
                raise HTTPException(422, 'Continue this projected task through the Log guided workflow')
            stored = catalog._get(session, body.project_id, TASK, identity)
            if body.expected_revision != stored['revision']:
                raise HTTPException(409, 'Task revision changed; reload before saving')
            value = dict(stored)
            if principal.mode == 'project' and stored.get('participant_mode') != 'project':
                value.update(_participant(principal))
            for key in ('source_ref', 'execution_id', 'observation_id'):
                supplied = getattr(body, key)
                if supplied is not None:
                    if stored[key] is not None and stored[key] != supplied:
                        raise HTTPException(409, 'Task evidence is pinned; start another task to select different evidence')
                    value[key] = supplied
            if body.result_opened is not None:
                if stored['result_opened'] and not body.result_opened:
                    raise HTTPException(409, 'A viewed result cannot be marked unopened')
                value['result_opened'] = body.result_opened
            if body.time_s is not None:
                value['time_s'] = body.time_s
            if body.sensor_key is not None:
                value['sensor_key'] = body.sensor_key
            value.update(revision=stored['revision'] + 1, updated_at=utcnow())
            result = _present(session, value)
            if value['module'] == 'sensor':
                value.update(sensor_key=result.get('sensor_key'), time_s=result['time_s'])
            catalog._put(session, body.project_id, TASK, identity, value)
            _remember(session, body.project_id, operation, payload, result)
            return result

    @api.get('/beta/feedback', tags=['Beta workspace'])
    def feedback_list(request: Request, project_id: str = 'default', task_id: str | None = None):
        project_authorize(request, db, project_id)
        with db.read_session() as session:
            if task_id:
                _task(db, request, session, project_id, task_id)
            return dict(schema_version='beta-feedback-list/1', items=[value for value in _feedback(session, project_id)
                if task_id is None or value.get('task_id') == task_id])

    @api.post('/beta/feedback', status_code=201, tags=['Beta workspace'], openapi_extra={'x-axiom-min-role': 'Engineer'})
    def feedback_create(body: FeedbackCreate, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = project_authorize(request, db, body.project_id, 'Engineer')
        with jobs._write(db) as session:
            operation, payload, previous = _operation(session, body.project_id, 'feedback', idempotency_key, body)
            if previous:
                return previous
            context, duration = {}, None
            if body.kind == 'completion' and not body.task_id:
                raise HTTPException(422, 'Completion feedback requires a completed task')
            if body.task_id:
                task = _task(db, request, session, body.project_id, body.task_id)
                if task['module'] != body.module:
                    raise HTTPException(422, 'Feedback module does not match the task')
                if body.execution_id is not None and body.execution_id != task['execution_id']:
                    raise HTTPException(409, 'Feedback execution differs from the task')
                if body.kind == 'completion' and task['status'] != 'completed':
                    raise HTTPException(409, 'Finish the linked task before reporting completion')
                if body.module == 'sensor' and body.time_s is not None and not task.get('execution_id'):
                    raise HTTPException(422, 'A Sensor feedback timestamp requires a linked execution')
                if (body.module == 'sensor' and task.get('observation_id') and body.time_s is not None
                        and body.time_s != task['time_s']):
                    raise HTTPException(409, 'Feedback timestamp differs from the linked Sensor observation')
                context = {key: task.get(key) for key in ('source_ref', 'execution_id', 'observation_id', 'sensor_key', 'guide_id', 'resume_url', 'execution_status', 'execution', 'source_id', 'source_hash', 'comparison_ref', 'batch_id', 'case_id')}
                context['task_revision'] = task['revision']
                context['task_stage'] = task['stage']
                duration = task.get('duration_s')
            elif body.execution_id:
                if body.module == 'log':
                    raise HTTPException(422, 'Link Log comparison context through its guided beta task')
                execution, source_ref, duration = _execution(session, body.project_id, body.module, body.execution_id)
                context = dict(source_ref=source_ref, execution={key: execution.get(key) for key in ('id', 'status', 'adapter_id', 'adapter_version', 'fidelity', 'input_hash', 'manifest_hash')},
                    execution_id=execution['id'], execution_status=execution['status'],
                    resume_url=_resume(dict(project_id=body.project_id, module=body.module, execution_id=execution['id'], time_s=body.time_s)))
            elif body.kind == 'completion':
                raise HTTPException(422, 'Completion feedback requires a completed task')
            if body.time_s is not None and (duration is None or body.time_s > duration):
                raise HTTPException(422, 'Timestamp requires a linked execution with a known bounded duration')
            if body.time_s is not None:
                page, _, raw_query = context['resume_url'].partition('?')
                query = dict(parse_qsl(raw_query))
                query['time_s'] = str(body.time_s)
                context['resume_url'] = page + '?' + urlencode(query)
            now = utcnow()
            if payload['stage'] is None and body.task_id:
                feedback_payload = dict(payload, stage=context['task_stage'])
            else:
                feedback_payload = payload
            value = dict(feedback_payload, schema_version='beta-feedback/1', id='betafeedback_' + digest([body.project_id, idempotency_key])[:32],
                revision=1, origin='common', status='received', triage_note=None, created_at=now, updated_at=now,
                context=context, **_participant(principal))
            catalog._put(session, body.project_id, FEEDBACK, value['id'], value)
            _remember(session, body.project_id, operation, payload, value)
            return value

    @api.post('/beta/feedback/{identity}/triage', tags=['Beta workspace'], openapi_extra={'x-axiom-operator-only': True})
    def triage(identity: str, body: FeedbackTriage, request: Request, idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=180)):
        principal = _operator(request)
        project_authorize(request, db, body.project_id)
        with jobs._write(db) as session:
            operation, payload, previous = _operation(session, body.project_id, 'triage:' + identity, idempotency_key, body)
            if previous:
                return previous
            is_log = identity.startswith('logfeedback_')
            value = (next((item for item in _feedback(session, body.project_id) if item['id'] == identity), None) if is_log else
                     catalog._get(session, body.project_id, FEEDBACK, identity))
            if value is None:
                raise HTTPException(404, 'Feedback not found in project')
            if value['revision'] != body.expected_revision:
                raise HTTPException(409, 'Feedback revision changed; reload before triage')
            result = dict(value, revision=value['revision'] + 1, status=body.status, triage_note=body.note,
                triaged_by=principal.user_label, updated_at=utcnow())
            if is_log:
                catalog._put(session, body.project_id, LOG_TRIAGE, identity, {key: result[key] for key in
                    ('revision', 'status', 'triage_note', 'triaged_by', 'updated_at', 'created_at')})
            else:
                catalog._put(session, body.project_id, FEEDBACK, identity, result)
            _remember(session, body.project_id, operation, payload, result)
            return result

    @api.get('/beta/overview', tags=['Beta workspace'], openapi_extra={'x-axiom-operator-only': True})
    def overview(request: Request, format: Literal['json', 'csv'] = 'json'):
        _operator(request)
        with db.read_session() as session:
            projects = [dict(id=row.id, name=row.name) for row in session.scalars(select(ProjectRow).order_by(ProjectRow.id))]
            tasks, feedback = [], []
            for project in projects:
                tasks.extend(_tasks(db, request, session, project['id']))
                feedback.extend(_feedback(session, project['id']))
            counts = dict(started=len(tasks), active=sum(t['status'] == 'active' for t in tasks),
                completed=sum(t['status'] == 'completed' for t in tasks), blocked=sum(t['status'] == 'blocked' for t in tasks),
                participants=len({t['participant_id'] for t in tasks if t.get('participant_mode') == 'project'}), feedback=len(feedback),
                needs_help=sum(f['kind'] == 'help' and f['status'] != 'resolved' for f in feedback))
            if format == 'json':
                return dict(schema_version='beta-overview/1', projects=projects, tasks=tasks, feedback=feedback, counts=counts, checked_at=utcnow())
            output = io.StringIO()
            columns = ['record_type', 'project_id', 'participant', 'id', 'module', 'title', 'status', 'stage', 'mode', 'execution_id',
                       'observation_id', 'completion_basis', 'kind', 'message', 'triage_note', 'created_at', 'updated_at']
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            for kind, values in (('task', tasks), ('feedback', feedback)):
                for value in values:
                    writer.writerow({key: log_tasks._csv_cell(kind if key == 'record_type' else value.get(key)) for key in columns})
            return Response(output.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="beta-overview.csv"'})
