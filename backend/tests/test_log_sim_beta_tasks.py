"""Guided beta uses actual CPU reference jobs; fixtures are authored MCAP."""
import io
import json

import pytest
from fastapi import APIRouter, Depends, Request
from mcap.reader import make_reader
from mcap.writer import Writer

from ade2e.adapters import Registry
from ade2e import log_sim
from ade2e.database import Settings
from ade2e.log_sim_beta_tasks import register_log_sim_beta_task_routes
from ade2e.log_sim_beta_packs import example_mcap
from ade2e.service import canonical_bytes
from ade2e.team_access import authenticate_request
from ade2e.worker import run_once
from test_log_sim_beta import beta_env, upload, register, selection
from test_object_demo import env


@pytest.fixture
def guided(beta_env):
    db, client, registry, project = beta_env

    def auth(request: Request):
        authenticate_request(request, db, Settings(api_token='test-bootstrap', team_mode=True, seed_examples=False),
                             request.headers.get('authorization', '')[7:])

    api = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    register_log_sim_beta_task_routes(api, db)
    client.app.include_router(api)
    return db, client, registry, project


def post(client, route, body, key, status=200):
    response = client.post('/api/v1/log-sim/' + route, json=body, headers={'Idempotency-Key': key})
    assert response.status_code == status, response.text
    return response.json()


def read(client, project, task):
    response = client.get('/api/v1/log-sim/beta-tasks/' + task['id'], params={'project_id': project})
    assert response.status_code == 200, response.text
    return response.json()


def progress(client, task, key, **kwargs):
    return post(client, 'beta-tasks/' + task['id'] + '/progress',
                dict(project_id=task['project_id'], expected_revision=task['revision'], **kwargs), key)


def begin(client, project, *, empty=False, key='task'):
    if empty:
        # Preserve exact timestamped ego samples but remove all objects: two equal
        # controller profiles produce no event or divergence candidates.
        output = io.BytesIO(); writer = Writer(output); writer.start()
        schema_ids, channel_ids = {}, {}
        for schema, channel, message in make_reader(io.BytesIO(example_mcap())).iter_messages(log_time_order=False):
            if schema.id not in schema_ids:
                schema_ids[schema.id] = writer.register_schema(schema.name, schema.encoding, schema.data)
            if channel.id not in channel_ids:
                channel_ids[channel.id] = writer.register_channel(channel.topic, channel.message_encoding, schema_ids[schema.id])
            value = json.loads(message.data)
            if schema.name == 'axiom.ObjectTracks.v1':
                value['objects'] = []
            writer.add_message(channel_ids[channel.id], message.log_time, canonical_bytes(value), message.publish_time, message.sequence)
        writer.finish()
        response = client.post('/api/v1/log-sim/uploads', params={'project_id': project}, content=output.getvalue(), headers={'Content-Type': 'application/octet-stream'})
        assert response.status_code == 201, response.text
        recording = response.json()
    else:
        recording = upload(client, project)
    source = register(client, selection(project, recording))
    full = client.get('/api/v1/log-sim/sources/' + source['id'], params={'project_id': project}).json()
    response = client.post('/api/v1/log-sim/packs/following-gap/binding', json=dict(project_id=project,
        source_id=source['id'], source_hash=full['source_hash'], note='Authored test fixture, not CARLA'),
        headers={'Authorization': 'Bearer test-bootstrap'})
    assert response.status_code == 200, response.text
    task = post(client, 'beta-tasks', dict(project_id=project, pack_id='following-gap'), key, 201)
    return task, source


def settings(client, task, *, equal=False, key='settings'):
    recipe = post(client, 'comparison-settings', dict(project_id=task['project_id'], name='Beta conditions',
        candidate={'time_headway_s': 1.5 if equal else 3.}), key, 201)
    ref = {key: recipe[key] for key in ('id', 'revision', 'sha256')}
    return progress(client, task, 'settings-' + key, comparison_ref=ref), ref


def batch(client, task, key='batch'):
    body = dict(project_id=task['project_id'], source_ids=[task['source_id']], comparison_ref=task['comparison_ref'])
    preview = post(client, 'batches/preview', body, key)
    value = post(client, 'batches', body | {'preview_hash': preview['preview_hash']}, key, 202)
    return progress(client, task, 'link-' + key, batch_id=value['id']), value


def finish_jobs(db, registry, value):
    for job in value['sources'][0]['execution_ids'].values():
        assert run_once(db, Registry(), 'guided-beta-reference', job_id=job, experiment_registry=registry)


def review(client, task, case_id, status='reviewed', expected_revision=0):
    return post(client, 'reviews', dict(project_id=task['project_id'], source_id=task['source_id'], case_id=case_id,
        expected_revision=expected_revision, status=status, note='Observed reference control requests only', time_s=1.), 'review-' + status, 201)


def test_live_actual_completion_and_restored_revision(guided):
    db, client, registry, project = guided
    task, _ = begin(client, project)
    assert task['stage'] == 'settings' and task['revision'] == 1
    assert post(client, 'beta-tasks', dict(project_id=project, pack_id='following-gap'), 'task', 201) == task
    task, ref = settings(client, task)
    second = post(client, 'comparison-settings', dict(project_id=project, id=ref['id'], expected_revision=1,
        name='Later edited settings', candidate={'time_headway_s': 5.}), 'settings-v2', 201)
    old = client.get('/api/v1/log-sim/comparison-settings/' + ref['id'], params={'project_id': project, 'revision': 1})
    assert old.status_code == 200 and old.json()['sha256'] == ref['sha256']
    assert old.json()['profiles']['candidate']['controller']['time_headway_s'] == 3.
    task, execution = batch(client, task)
    assert task['stage'] == 'execution' and task['execution_status'] == 'queued'
    finish_jobs(db, registry, execution)
    task = read(client, project, task)
    assert task['stage'] == 'investigation' and task['execution_status'] == 'completed'
    report = client.get('/api/v1/log-sim/batches/' + task['batch_id'], params={'project_id': project}).json()
    bundle = post(client, 'batches/' + task['batch_id'] + '/case-bundles', dict(project_id=project,
        name='Guided investigation', report_hash=report['report_hash'], candidate_ids=[report['candidates'][0]['id']]), 'bundle', 201)
    case_id = bundle['items'][0]['case_id']
    task = progress(client, task, 'investigate', result_opened=True, case_id=case_id)
    review(client, task, case_id, 'investigate')
    assert read(client, project, task)['stage'] == 'review'
    review(client, task, case_id, 'reviewed', 1)
    completed = read(client, project, task)
    assert completed['status'] == completed['stage'] == 'completed'
    assert completed['revision'] == task['revision'] and completed['comparison_ref'] == ref
    assert second['revision'] == 2  # Latest recipe edits never rewrite this task's input.
    items = client.get('/api/v1/log-sim/beta-tasks', params={'project_id': project}).json()['items']
    assert items == [completed]


def test_no_candidates_can_complete_with_manual_observation_case(guided):
    db, client, registry, project = guided
    task, _ = begin(client, project, empty=True)
    task, _ = settings(client, task, equal=True)
    task, execution = batch(client, task)
    finish_jobs(db, registry, execution)
    report = client.get('/api/v1/log-sim/batches/' + task['batch_id'], params={'project_id': project}).json()
    assert report['status'] == 'completed' and report['candidates'] == []
    case = post(client, 'sources/' + task['source_id'] + '/cases', dict(project_id=project,
        title='No observed differences', note='Equal profiles: recorded observation', start_time_s=0., end_time_s=3.), 'manual', 201)
    task = progress(client, task, 'manual-case', result_opened=True, case_id=case['id'])
    review(client, task, case['id'])
    assert read(client, project, task)['status'] == 'completed'
    response = post(client, 'beta-feedback', dict(project_id=project, task_id=task['id'], stage='completed',
        kind='completion', message='Completed without help', needs_help=False), 'completion', 201)
    assert response['batch_id'] == task['batch_id'] and response['source_hash'] == task['source_hash']
    overview = client.get('/api/v1/log-sim/beta-overview', headers={'Authorization': 'Bearer test-bootstrap'}).json()
    assert overview['counts']['completed_without_help'] == 1


def test_wrong_refs_stale_writes_idempotency_and_execution_pins(guided):
    db, client, registry, project = guided
    original, _ = begin(client, project)
    task, ref = settings(client, original)
    assert progress(client, original, 'settings-settings', comparison_ref=ref) == task
    post(client, 'beta-tasks/' + task['id'] + '/progress', dict(project_id=project, expected_revision=1,
        comparison_ref=ref), 'stale', 409)
    post(client, 'beta-tasks/' + task['id'] + '/progress', dict(project_id=project, expected_revision=2,
        comparison_ref=ref | {'sha256': '0'*64}), 'bad-hash', 409)
    task, execution = batch(client, task)
    post(client, 'beta-tasks/' + task['id'] + '/progress', dict(project_id=project, expected_revision=task['revision'],
        result_opened=True), 'too-early', 409)
    changed = post(client, 'comparison-settings', dict(project_id=project, name='Different conditions'), 'other-settings', 201)
    other_ref = {key: changed[key] for key in ('id', 'revision', 'sha256')}
    post(client, 'beta-tasks/' + task['id'] + '/progress', dict(project_id=project, expected_revision=task['revision'],
        comparison_ref=other_ref), 'changed-settings', 409)
    post(client, 'beta-tasks/' + task['id'] + '/progress', dict(project_id=project, expected_revision=task['revision'],
        source_id='another-source'), 'changed-source', 409)
    other = post(client, 'beta-tasks', dict(project_id=project, pack_id='following-gap'), 'other-task', 201)
    other = progress(client, other, 'other-ref', comparison_ref=other_ref)
    post(client, 'beta-tasks/' + other['id'] + '/progress', dict(project_id=project, expected_revision=other['revision'],
        batch_id=execution['id']), 'wrong-batch-settings', 409)
    finish_jobs(db, registry, execution)
    other, foreign_batch = batch(client, other, 'other-batch')
    finish_jobs(db, registry, foreign_batch)
    report = client.get('/api/v1/log-sim/batches/' + execution['id'], params={'project_id': project}).json()
    bundle = post(client, 'batches/' + execution['id'] + '/case-bundles', dict(project_id=project,
        name='Original candidate', report_hash=report['report_hash'], candidate_ids=[report['candidates'][0]['id']]), 'candidate', 201)
    post(client, 'beta-tasks/' + other['id'] + '/progress', dict(project_id=project, expected_revision=other['revision'],
        case_id=bundle['items'][0]['case_id']), 'wrong-case', 409)


def test_unbound_input_exercise_and_feedback_recovery(guided):
    _, client, _, project = guided
    post(client, 'beta-tasks', dict(project_id=project, pack_id='following-gap'), 'unbound', 409)
    task = post(client, 'beta-tasks', dict(project_id=project, pack_id='input-errors'), 'input-task', 201)
    assert task['stage'] == 'source' and task['source_id'] is None
    body = dict(project_id=project, task_id=task['id'], stage='source', kind='blocked',
                message='=The input is missing objects', needs_help=True)
    feedback = post(client, 'beta-feedback', body, 'blocked', 201)
    assert post(client, 'beta-feedback', body, 'blocked', 201) == feedback
    post(client, 'beta-feedback', body | {'message': 'Another request'}, 'blocked', 409)
    assert read(client, project, task)['status'] == 'blocked'
    post(client, 'beta-feedback', body | {'kind': 'completion'}, 'too-early-completion', 409)
    source = register(client, selection(project, upload(client, project)))
    task = progress(client, task, 'registered', source_id=source['id'])
    assert task['stage'] == 'settings' and task['status'] == 'active'
    report = client.get('/api/v1/log-sim/beta-overview', headers={'Authorization': 'Bearer test-bootstrap'}).json()
    assert report['counts']['started'] == 1 and report['feedback'][0]['message'] == body['message']
    csv = client.get('/api/v1/log-sim/beta-overview?format=csv', headers={'Authorization': 'Bearer test-bootstrap'})
    assert "'=The input" in csv.text
    assert 'test-bootstrap' not in json.dumps(report) and 'Authorization' not in json.dumps(report)


def test_project_permissions_on_tasks_feedback_and_operator_overview(guided):
    _, client, _, project = guided
    task, _ = begin(client, project)
    assert client.get('/api/v1/log-sim/beta-tasks/' + task['id'], params={'project_id': 'default'}).status_code == 404
    assert client.get('/api/v1/log-sim/beta-overview').status_code == 403
    for role in ('Viewer', 'Admin'):
        token = client.post('/api/v1/projects/' + project + '/tokens', json={'user_label': role, 'role': role},
            headers={'Authorization': 'Bearer test-bootstrap'}).json()['token']
        client.headers['Authorization'] = 'Bearer ' + token
        assert client.get('/api/v1/log-sim/beta-overview').status_code == 403
        assert read(client, project, task)['id'] == task['id']
        if role == 'Viewer':
            post(client, 'beta-tasks', dict(project_id=project, pack_id='input-errors'), 'no-write', 403)
            post(client, 'beta-feedback', dict(project_id=project, task_id=task['id'], stage='source', kind='blocked', message='Help'), 'no-feedback', 403)
    client.headers.pop('Authorization')
    assert client.get('/api/v1/log-sim/beta-tasks', params={'project_id': project}).status_code == 401


def test_failed_job_requires_explicit_retry_and_keeps_task_execution(guided, monkeypatch):
    db, client, registry, project = guided
    task, _ = begin(client, project)
    task, _ = settings(client, task)
    task, execution = batch(client, task)
    handler = registry.get('reference-planning-replay')
    execute = handler.execute

    def authored_failure(*args, **kwargs):
        raise RuntimeError('Authored worker outage for recovery contract, not an engine run')

    monkeypatch.setattr(handler, 'execute', authored_failure)
    finish_jobs(db, registry, execution)
    failed = read(client, project, task)
    assert failed['status'] == 'blocked' and failed['stage'] == 'execution'
    assert failed['execution_status'] == 'completed_with_errors'
    monkeypatch.setattr(handler, 'execute', execute)
    retried = post(client, 'batches/' + task['batch_id'] + '/retry', {'project_id': project}, 'retry', 202)
    assert retried['sources'][0]['attempts'] == [execution['sources'][0]['comparison_id']]
    assert post(client, 'batches/' + task['batch_id'] + '/retry', {'project_id': project}, 'retry', 202) == retried
    finish_jobs(db, registry, retried)
    restored = read(client, project, task)
    assert restored['stage'] == 'investigation' and restored['batch_id'] == task['batch_id']
    assert restored['revision'] == task['revision']


def test_same_source_id_cannot_refresh_pinned_hash(guided, monkeypatch):
    _, client, _, project = guided
    task, _ = begin(client, project)
    original_source = log_sim._source

    def changed_source(session, project_id, identity):
        value = original_source(session, project_id, identity)
        return value | {'source_hash': 'f'*64} if identity == task['source_id'] else value

    monkeypatch.setattr(log_sim, '_source', changed_source)
    post(client, 'beta-tasks/' + task['id'] + '/progress', dict(project_id=project,
        expected_revision=task['revision'], source_id=task['source_id']), 'cannot-repin', 409)
    overview = client.get('/api/v1/log-sim/beta-overview', headers={'Authorization': 'Bearer test-bootstrap'}).json()
    assert overview['tasks'][0]['status'] == 'blocked'
    assert overview['tasks'][0]['source_hash'] == task['source_hash']
