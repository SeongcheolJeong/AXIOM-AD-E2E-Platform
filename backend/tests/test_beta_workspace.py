"""Common beta workflow checks use authored handler fixtures, not native CARLA."""
import json
import pytest
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from ade2e.beta_workspace import FEEDBACK, LOG_TRIAGE, TASK, register_beta_workspace_routes
from ade2e.database import Settings
from ade2e import log_sim_beta_tasks, log_sim_replays
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.team_access import authenticate_request
from test_hil_beta import env as hil_env, base_env, save, ref, execute, attach, note, body as hil_body


@pytest.fixture
def env(hil_env):
    db, client, _, registry, _ = hil_env
    def auth(request: Request):
        authenticate_request(request, db, Settings(api_token='test-bootstrap', team_mode=True, seed_examples=False),
                             request.headers.get('authorization', '')[7:])
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    register_beta_workspace_routes(api, db, registry)
    log_sim_beta_tasks.register_log_sim_beta_task_routes(api, db)
    client.app.include_router(api)
    return hil_env


def post(env, path, value, key='request', status=200, token=None):
    headers = {'Idempotency-Key': key}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    response = env[1].post('/api/v1/beta/' + path, json=value, headers=headers)
    assert response.status_code == status, response.text
    return response.json()


def start(env, key='task', **kwargs):
    return post(env, 'tasks', dict(project_id='default', module='hil', title='Fault investigation', mode='new_execution', **kwargs), key, 201)


def progress(env, task, key='progress', status=200, **kwargs):
    return post(env, 'tasks/' + task['id'] + '/progress', dict(project_id=task['project_id'], expected_revision=task['revision'], **kwargs), key, status)


def token(env, project='default', role='Engineer'):
    response = env[1].post('/api/v1/projects/' + project + '/tokens', json=dict(user_label='Beta participant', role=role))
    assert response.status_code == 201, response.text
    return response.json()['token']


def test_readiness_never_treats_configured_handler_as_live_worker(env):
    response = env[1].get('/api/v1/beta/readiness').json()
    hil = next(row for row in response['items'] if row['module'] == 'hil')
    assert response['checked_at'] and hil['new_execution']['status'] == 'unknown'
    assert hil['new_execution']['configured'] and not hil['new_execution']['worker_verified']
    assert hil['retained_playback']['status'] == 'empty'
    assert env[2].probes == 0
    viewer = token(env, role='Viewer')
    response = env[1].get('/api/v1/beta/readiness', headers={'Authorization': 'Bearer ' + viewer})
    assert response.status_code == 200
    assert all(row['authoring']['status'] == row['new_execution']['status'] == 'blocked' for row in response.json()['items'])


def test_task_idempotency_stale_revision_and_exact_hil_source(env):
    test = save(env)
    task = start(env)
    assert start(env) == task
    post(env, 'tasks', dict(project_id='default', module='hil', title='Changed', mode='new_execution'), 'task', 409)
    updated = progress(env, task, source_ref=ref(test))
    assert progress(env, task, source_ref=ref(test)) == updated
    progress(env, task, key='stale', status=409, source_ref=ref(test))
    assert updated['revision'] == 2 and updated['stage'] == 'execution'
    assert 'test=' + test['id'] in updated['resume_url'] and 'revision=1' in updated['resume_url']
    wrong = start(env, key='wrong')
    progress(env, wrong, status=409, source_ref={**ref(test), 'sha256': 'f' * 64})
    progress(env, wrong, key='missingrev', status=404, source_ref={**ref(test), 'revision': 88})
    choices = env[1].get('/api/v1/beta/evidence?module=hil').json()['items']
    assert any(item['source_ref'] == ref(test) for item in choices)


def test_new_execution_complete_and_bounded_resume_observation(env):
    task = start(env)
    test = save(env)
    execution = execute(env, test)
    assert attach(env, test, execution).status_code == 201
    observation = note(env, test, execution)
    assert observation.status_code == 201, observation.text
    task = progress(env, task, source_ref=ref(test), execution_id=execution, observation_id=observation.json()['id'], result_opened=True, time_s=.12)
    assert task['status'] == 'completed' and task['completion_basis'] == 'new_execution_observation'
    assert task['execution']['fidelity'] == 'actual_carla_software_dut'  # Fixture metadata only.
    assert 'result=' + execution in task['resume_url'] and 'time_s=0.12' in task['resume_url']
    assert env[1].get('/api/v1/beta/tasks/' + task['id']).json() == task
    assert task in env[1].get('/api/v1/beta/tasks').json()['items']
    progress(env, task, key='time', status=422, time_s=19)
    report = post(env, 'feedback', dict(project_id='default', module='hil', task_id=task['id'], kind='completion', message='Completed with the fixture', time_s=.12), 'completed', 201)
    assert report['context']['task_revision'] == task['revision'] and report['context']['execution_id'] == execution


def test_retained_execution_cannot_complete_new_task_or_wrong_test(env):
    test = save(env)
    execution = execute(env, test)
    task = start(env)
    progress(env, task, status=409, source_ref=ref(test), execution_id=execution)
    retained = post(env, 'tasks', dict(project_id='default', module='hil', title='Retained playback', mode='retained_playback'), 'retained', 201)
    retained = progress(env, retained, key='retained-link', source_ref=ref(test), execution_id=execution, result_opened=True)
    assert retained['status'] == 'active' and retained['mode'] == 'retained_playback'
    changed = save(env, {**hil_body(env), 'id': test['id'], 'expected_revision': 1, 'parameters': {'delay_ms': 400}}, 'changed')
    second = post(env, 'tasks', dict(project_id='default', module='hil', title='Wrong source', mode='retained_playback'), 'wrong-source', 201)
    progress(env, second, key='wrong-link', status=409, source_ref=ref(changed), execution_id=execution)


def test_cross_project_and_roles_protect_reads_writes_and_operator_exports(env):
    task = start(env)
    other = env[1].post('/api/v1/projects', json={'name': 'Other participant'}).json()['id']
    own_token = token(env)
    foreign_token = token(env, project=other)
    viewer = token(env, role='Viewer')
    path = '/api/v1/beta/tasks/' + task['id']
    assert env[1].get(path, headers={'Authorization': 'Bearer ' + foreign_token}).status_code == 404
    assert env[1].get(path, params={'project_id': other}, headers={'Authorization': 'Bearer ' + foreign_token}).status_code == 404
    for route in ('overview', 'overview?format=csv'):
        assert env[1].get('/api/v1/beta/' + route, headers={'Authorization': 'Bearer ' + own_token}).status_code == 403
    post(env, 'tasks', dict(project_id='default', module='hil', title='No write', mode='new_execution'), 'viewer', 403, viewer)
    # A foreign saved record cannot be borrowed by an operator into another project.
    test = save(env)
    foreign = post(env, 'tasks', dict(project_id=other, module='hil', title='Foreign', mode='new_execution'), 'foreign', 201)
    progress(env, foreign, status=404, source_ref=ref(test))
    overview = env[1].get('/api/v1/beta/overview').json()
    assert overview['counts']['participants'] == 0  # operator assignment is not human participation
    owned = post(env, 'tasks/' + task['id'] + '/progress', dict(project_id='default', expected_revision=1, source_ref=ref(test)), 'participant', token=own_token)
    assert owned['participant_mode'] == 'project'
    assert env[1].get('/api/v1/beta/overview').json()['counts']['participants'] == 1


def test_feedback_triage_revision_context_and_csv_formula_protection(env):
    task = start(env)
    report = post(env, 'feedback', dict(project_id='default', module='hil', task_id=task['id'], kind='help', message='=Do not execute as spreadsheet formula', stage='authoring'), 'help', 201)
    assert report['context']['task_revision'] == 1
    engineer = token(env)
    triage = dict(project_id='default', expected_revision=1, status='investigating', note='Checking configuration')
    post(env, 'feedback/' + report['id'] + '/triage', triage, 'triage', 403, engineer)
    updated = post(env, 'feedback/' + report['id'] + '/triage', triage, 'triage')
    assert updated['revision'] == 2
    assert post(env, 'feedback/' + report['id'] + '/triage', triage, 'triage') == updated
    post(env, 'feedback/' + report['id'] + '/triage', triage, 'stale', 409)
    assert env[1].get('/api/v1/beta/feedback').json()['items'][0]['status'] == 'investigating'
    csv = env[1].get('/api/v1/beta/overview?format=csv')
    assert csv.status_code == 200 and "'=Do not execute" in csv.text
    post(env, 'feedback', dict(project_id='default', module='sensor', kind='completion', message='Not a task', execution_id='missing'), 'no-completion', 422)
    post(env, 'feedback', dict(project_id='default', module='hil', kind='help', message='password=secret'), 'secret', 422)
    post(env, 'feedback', dict(project_id='default', module='hil', task_id=task['id'], kind='help', message='Time without execution', time_s=2), 'unboundtime', 422)


def test_log_task_projection_and_triage_keep_original_domain_record(env):
    task = post(env, 'tasks', dict(project_id='default', module='log', title='Input exercise', mode='new_execution', pack_id='input-errors'), 'log', 201)
    assert task['origin'] == 'log_guided' and task['id'].startswith('logtask_')
    with env[0].read_session() as session:
        assert not list(session.scalars(select(SimulationCatalogRevisionRow).where(SimulationCatalogRevisionRow.kind == TASK)))
    assert env[1].get('/api/v1/log-sim/beta-tasks/' + task['id']).json()['id'] == task['id']
    report = env[1].post('/api/v1/log-sim/beta-feedback', json=dict(project_id='default', task_id=task['id'], stage='source', kind='blocked', message='Need upload guidance'), headers={'Idempotency-Key': 'log-help'})
    assert report.status_code == 201, report.text
    original = report.json()
    projected = env[1].get('/api/v1/beta/feedback').json()['items'][0]
    assert projected['kind'] == 'help' and 'task=' + task['id'] in projected['context']['resume_url']
    updated = post(env, 'feedback/' + projected['id'] + '/triage', dict(project_id='default', expected_revision=1, status='resolved', note='Guided upload'), 'resolve')
    assert updated['status'] == 'resolved'
    assert env[1].get('/api/v1/log-sim/beta-feedback').json()['items'] == [original]
    assert env[1].get('/api/v1/beta/overview').json()['counts']['needs_help'] == 0
    progress(env, task, key='log-not-common', status=422, result_opened=True)


def test_policy_is_applied_to_readiness_without_probing(env):
    response = env[1].post('/api/v1/projects/default/object-beta-policy', json=dict(enabled=True, resource_id='carla-city-2100'))
    assert response.status_code == 200, response.text
    values = env[1].get('/api/v1/beta/readiness').json()['items']
    assert all(item['new_execution']['status'] == 'blocked' for item in values)
    assert env[2].probes == 0


def test_same_participant_across_log_and_common_tasks_counts_once(env):
    engineer = token(env)
    post(env, 'tasks', dict(project_id='default', module='object', title='Object exercise', mode='new_execution'), 'object-person', 201, engineer)
    log = post(env, 'tasks', dict(project_id='default', module='log', title='Log exercise', mode='new_execution', pack_id='input-errors'), 'log-person', 201, engineer)
    overview = env[1].get('/api/v1/beta/overview').json()
    assert overview['counts']['participants'] == 1
    assert len({task['participant_id'] for task in overview['tasks']}) == 1
    assert env[1].get('/api/v1/log-sim/beta-tasks/' + log['id']).json()['participant_id'] != log['participant_id']
    assigned = post(env, 'tasks', dict(project_id='default', module='log', title='Assigned exercise', mode='new_execution', pack_id='input-errors'), 'assigned-log', 201)
    response = env[1].post('/api/v1/log-sim/beta-tasks/' + assigned['id'] + '/progress',
        json=dict(project_id='default', expected_revision=1), headers={'Idempotency-Key': 'claim-log', 'Authorization': 'Bearer ' + engineer})
    assert response.status_code == 200, response.text
    assert response.json()['created_by']['participant'] == 'bootstrap-operator'
    projected = env[1].get('/api/v1/beta/tasks/' + assigned['id']).json()
    assert projected['participant_id'] == log['participant_id']
    assert env[1].get('/api/v1/beta/overview').json()['counts']['participants'] == 1


def test_object_exact_revision_and_wrong_project_are_validated(env):
    source_ref = env[4]
    task = post(env, 'tasks', dict(project_id='default', module='object', title='Object authoring', mode='new_execution'), 'object', 201)
    task = progress(env, task, source_ref=source_ref)
    assert 'mode=city' in task['resume_url'] and 'scenario=' + source_ref['id'] in task['resume_url']
    assert task['steps'][0]['complete']
    other = post(env, 'tasks', dict(project_id='default', module='object', title='Wrong hash', mode='new_execution'), 'object-other', 201)
    progress(env, other, key='wrong-object-hash', status=409, source_ref={**source_ref, 'configuration_hash': '0' * 64})
    progress(env, other, key='missing-object-rev', status=404, source_ref={**source_ref, 'revision': 999})
    choices = env[1].get('/api/v1/beta/evidence?module=object').json()['items']
    assert any(choice['source_ref'] == source_ref for choice in choices)
    project = env[1].post('/api/v1/projects', json={'name': 'Separate object project'}).json()['id']
    foreign = post(env, 'tasks', dict(project_id=project, module='object', title='Foreign source', mode='new_execution'), 'object-foreign', 201)
    progress(env, foreign, key='wrong-object-project', status=404, source_ref=source_ref)


def test_failed_execution_blocks_completion_but_allows_help_context(env):
    task, test = start(env), save(env)
    env[2].fail = True
    execution = execute(env, test)
    task = progress(env, task, source_ref=ref(test), execution_id=execution, result_opened=True)
    assert task['status'] == 'blocked' and not task['steps'][1]['complete']
    report = post(env, 'feedback', dict(project_id='default', module='hil', task_id=task['id'], kind='help', message='Execution failed'), 'failure-help', 201)
    assert report['stage'] == task['stage'] and report['context']['execution_status'] == 'failed'
    post(env, 'feedback', dict(project_id='default', module='hil', task_id=task['id'], kind='completion', message='Not completed'), 'failure-complete', 409)


def test_observation_cannot_be_attached_to_another_execution(env):
    test = save(env)
    first = execute(env, test)
    assert attach(env, test, first).status_code == 201
    observation = note(env, test, first).json()
    task = start(env)
    second = execute(env, test, key='second-execution')
    progress(env, task, status=409, source_ref=ref(test), execution_id=second, observation_id=observation['id'])
    # Every new common catalog kind and identity fits the existing SQL schema.
    with env[0].read_session() as session:
        rows = list(session.scalars(select(SimulationCatalogRevisionRow).where(SimulationCatalogRevisionRow.kind.in_([TASK, FEEDBACK, LOG_TRIAGE, 'beta-write']))))
        assert all(len(row.kind) <= 20 and len(row.id) <= 64 for row in rows)


def test_taskless_execution_feedback_validates_kind_and_project(env):
    test = save(env)
    execution = execute(env, test)
    feedback = post(env, 'feedback', dict(project_id='default', module='hil', execution_id=execution,
        kind='issue', message='Retained response needs investigation', time_s=.12), 'taskless', 201)
    assert feedback['context']['execution_id'] == execution
    assert feedback['context']['execution']['input_hash']
    assert 'result=' + execution in feedback['context']['resume_url']
    post(env, 'feedback', dict(project_id='default', module='sensor', execution_id=execution,
        kind='issue', message='Wrong module'), 'wrongmodule', 422)
    project = env[1].post('/api/v1/projects', json={'name': 'Other reporter'}).json()['id']
    post(env, 'feedback', dict(project_id=project, module='hil', execution_id=execution,
        kind='issue', message='Wrong project'), 'wrongproject', 404)
    task = start(env)
    post(env, 'feedback', dict(project_id='default', module='hil', task_id=task['id'], execution_id=execution,
        kind='issue', message='Unlinked task execution'), 'mismatchedtask', 409)
