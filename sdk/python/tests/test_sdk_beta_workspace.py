"""Shared beta API transport and explicit retries; no engine or human acceptance."""
import json

import httpx
import pytest

from axiom_sdk import AxiomClient, AxiomError


OBJECT_REF = dict(id='scenario_a', revision=2, configuration_hash='a' * 64, manifest_hash='b' * 64)
HIL_REF = dict(id='hil_test_a', revision=3, sha256='c' * 64)


def test_workspace_guidance_feedback_and_operator_transport():
    calls = []
    csv = 'project_id,task_id,stage\nparticipant_a,task_a,observation\n'

    def respond(request):
        calls.append(request)
        if request.url.params.get('format') == 'csv':
            return httpx.Response(200, text=csv)
        return httpx.Response(200, json={'id': 'task_a', 'revision': 2})

    with AxiomClient(token='participant-token', transport=httpx.MockTransport(respond)) as client:
        client.beta_readiness(project_id='participant_a')
        client.beta_evidence('object', project_id='participant_a')
        client.beta_tasks(project_id='participant_a')
        client.beta_task('task_a', project_id='participant_a')
        client.create_beta_task('object', title='First comparison', project_id='participant_a',
            mode='retained_playback', idempotency_key='start')
        client.create_beta_task('log', title='Log task', pack_id='following-gap',
            project_id='participant_a', idempotency_key='log-start')
        client.update_beta_task('task_a', project_id='participant_a', expected_revision=1,
            source_ref=OBJECT_REF, execution_id='job_a', observation_id='note_a',
            result_opened=True, time_s=0, idempotency_key='progress')
        client.update_beta_task('task_a', project_id='participant_a', expected_revision=2,
            result_opened=False, idempotency_key='close-view')
        client.beta_feedback(project_id='participant_a', task_id='task_a')
        client.beta_feedback(project_id='participant_a')
        client.create_beta_feedback('object', project_id='participant_a', task_id='task_a',
            kind='help', message='Cannot find observation', stage='observation',
            error='Request failed', time_s=1.25, idempotency_key='help')
        client.create_beta_feedback('sensor', project_id='participant_a', execution_id='sensor_a',
            kind='issue', message='Playback stopped', time_s=2, idempotency_key='sensor-issue')
        client.triage_beta_feedback('feedback_a', project_id='participant_a', expected_revision=1,
            status='investigating', note='Opened the attached replay', idempotency_key='triage')
        assert client.beta_overview() == {'id': 'task_a', 'revision': 2}
        assert client.beta_overview(format='csv') == csv

    assert [(call.method, call.url.path) for call in calls] == [
        ('GET', '/api/v1/beta/readiness'), ('GET', '/api/v1/beta/evidence'),
        ('GET', '/api/v1/beta/tasks'), ('GET', '/api/v1/beta/tasks/task_a'),
        ('POST', '/api/v1/beta/tasks'), ('POST', '/api/v1/beta/tasks'),
        ('POST', '/api/v1/beta/tasks/task_a/progress'), ('POST', '/api/v1/beta/tasks/task_a/progress'),
        ('GET', '/api/v1/beta/feedback'), ('GET', '/api/v1/beta/feedback'),
        ('POST', '/api/v1/beta/feedback'), ('POST', '/api/v1/beta/feedback'),
        ('POST', '/api/v1/beta/feedback/feedback_a/triage'),
        ('GET', '/api/v1/beta/overview'), ('GET', '/api/v1/beta/overview'),
    ]
    assert all(call.headers['Authorization'] == 'Bearer participant-token' for call in calls)
    assert dict(calls[1].url.params) == {'project_id': 'participant_a', 'module': 'object'}
    assert json.loads(calls[4].content) == dict(project_id='participant_a', module='object',
        title='First comparison', mode='retained_playback')
    assert json.loads(calls[5].content)['pack_id'] == 'following-gap'
    assert json.loads(calls[6].content) == dict(project_id='participant_a', expected_revision=1,
        source_ref=OBJECT_REF, execution_id='job_a', observation_id='note_a', result_opened=True, time_s=0)
    assert json.loads(calls[7].content) == dict(project_id='participant_a', expected_revision=2, result_opened=False)
    assert calls[8].url.params['task_id'] == 'task_a' and 'task_id' not in calls[9].url.params
    assert json.loads(calls[10].content) == dict(project_id='participant_a', module='object',
        task_id='task_a', kind='help', message='Cannot find observation', stage='observation',
        error='Request failed', time_s=1.25)
    assert json.loads(calls[11].content)['execution_id'] == 'sensor_a'
    assert json.loads(calls[12].content) == dict(project_id='participant_a', expected_revision=1,
        status='investigating', note='Opened the attached replay')
    assert [calls[index].headers['Idempotency-Key'] for index in (4, 5, 6, 7, 10, 11, 12)] == [
        'start', 'log-start', 'progress', 'close-view', 'help', 'sensor-issue', 'triage']
    assert 'project_id' not in calls[13].url.params


def test_hil_pin_is_forwarded_without_replacing_exact_revision():
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.update_beta_task('task_hil', expected_revision=1, source_ref=HIL_REF, idempotency_key='pin-hil')
    assert json.loads(calls[0].content)['source_ref'] == HIL_REF


def test_sensor_guidance_and_exact_placement_context_use_common_api():
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True})
    source = dict(id='layout_one', revision=2, sha256='d' * 64)
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.beta_evidence('sensor', project_id='sensor_project')
        client.create_beta_task('sensor', project_id='sensor_project', title='Radar direction',
            mode='retained_playback', guide_id='radar_direction', idempotency_key='sensor-task')
        client.update_beta_task('sensor_task', project_id='sensor_project', expected_revision=1,
            source_ref=source, execution_id='sensor_run', observation_id='sensor_note',
            sensor_key='radar', time_s=19.9, result_opened=True, idempotency_key='sensor-context')
    assert dict(calls[0].url.params) == {'project_id': 'sensor_project', 'module': 'sensor'}
    assert json.loads(calls[1].content) == dict(project_id='sensor_project', module='sensor',
        title='Radar direction', mode='retained_playback', guide_id='radar_direction')
    assert json.loads(calls[2].content) == dict(project_id='sensor_project', expected_revision=1,
        source_ref=source, execution_id='sensor_run', observation_id='sensor_note', sensor_key='radar',
        time_s=19.9, result_opened=True)
    assert calls[2].headers['Idempotency-Key'] == 'sensor-context'


@pytest.mark.parametrize('invoke', [
    lambda c: c.beta_readiness(project_id='../other'),
    lambda c: c.beta_evidence('neural'),
    lambda c: c.beta_task('../other'),
    lambda c: c.beta_feedback(task_id='https://other/task'),
    lambda c: c.create_beta_task('neural', title='Unsupported', idempotency_key='start'),
    lambda c: c.create_beta_task('sensor', title='T', guide_id='unsupported', idempotency_key='start'),
    lambda c: c.create_beta_task('object', title='T', guide_id='camera_direction', idempotency_key='start'),
    lambda c: c.update_beta_task('task', expected_revision=1, sensor_key='left', idempotency_key='update'),
    lambda c: c.create_beta_task('object', title=' ', idempotency_key='start'),
    lambda c: c.create_beta_task('object', title='x' * 181, idempotency_key='start'),
    lambda c: c.create_beta_task('object', title='T', mode='complete', idempotency_key='start'),
    lambda c: c.create_beta_task('object', title='T', idempotency_key=' '),
    lambda c: c.create_beta_task('log', title='T', idempotency_key='start'),
    lambda c: c.create_beta_task('log', title='T', pack_id='p', mode='retained_playback', idempotency_key='start'),
    lambda c: c.update_beta_task('task', expected_revision=0, idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=True, idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=1, result_opened=1, idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=1, source_ref={'id': 's'}, idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=1, source_ref={**HIL_REF, 'revision': True}, idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=1, source_ref={**HIL_REF, 'sha256': 'bad'}, idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=1, source_ref={**OBJECT_REF, 'other': 1}, idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=1, time_s=float('nan'), idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=1, time_s=True, idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=1, time_s=-1, idempotency_key='update'),
    lambda c: c.update_beta_task('task', expected_revision=1, time_s=86401, idempotency_key='update'),
    lambda c: c.create_beta_feedback('object', kind='passed', message='M', idempotency_key='f'),
    lambda c: c.create_beta_feedback('other', kind='issue', message='M', idempotency_key='f'),
    lambda c: c.create_beta_feedback('object', kind='issue', message=' ', idempotency_key='f'),
    lambda c: c.create_beta_feedback('object', kind='issue', message='M', stage='', idempotency_key='f'),
    lambda c: c.create_beta_feedback('object', kind='issue', message='M', error=' ', idempotency_key='f'),
    lambda c: c.create_beta_feedback('object', kind='issue', message='M', stage='x' * 81, idempotency_key='f'),
    lambda c: c.create_beta_feedback('object', kind='issue', message='M', error='x' * 1001, idempotency_key='f'),
    lambda c: c.create_beta_feedback('object', kind='issue', message='M', time_s=1, idempotency_key='f'),
    lambda c: c.triage_beta_feedback('f', expected_revision=1, status='closed', idempotency_key='triage'),
    lambda c: c.triage_beta_feedback('f', expected_revision=0, status='resolved', idempotency_key='triage'),
    lambda c: c.beta_overview(format='html'),
])
def test_invalid_request_is_rejected_before_transport(invoke):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid request reached transport'))) as client:
        with pytest.raises(ValueError):
            invoke(client)


@pytest.mark.parametrize('status', [403, 409, 503])
def test_failed_progress_is_not_automatically_retried(status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={'detail': 'Retain the same logical request key'})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AxiomError) as caught:
            client.update_beta_task('task', expected_revision=1, idempotency_key='one-intent', source_ref=HIL_REF)
    assert caught.value.status_code == status
    assert len(calls) == 1
    assert calls[0].headers['Idempotency-Key'] == 'one-intent'
