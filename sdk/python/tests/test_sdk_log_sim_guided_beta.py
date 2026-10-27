"""Guided beta SDK wire contract; no simulator or human acceptance claim."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_guided_tasks_progress_feedback_and_operator_export():
    calls = []
    def reply(request):
        calls.append(request)
        if request.url.params.get('format') == 'csv':
            return httpx.Response(200, text='task_id,stage\ntask_a,review\n')
        return httpx.Response(200, json={'id': 'task_a', 'revision': 2})
    pin = {'id': 'settings_a', 'revision': 2, 'sha256': 'a' * 64}
    with AxiomClient(transport=httpx.MockTransport(reply)) as client:
        client.create_log_sim_beta_task('following-gap', project_id='beta', idempotency_key='start')
        client.log_sim_beta_tasks(project_id='beta')
        client.log_sim_beta_task('task_a', project_id='beta')
        client.log_sim_comparison_setting('settings_a', project_id='beta', revision=2)
        client.update_log_sim_beta_task('task_a', project_id='beta', expected_revision=1,
            comparison_ref=pin, batch_id='batch_a', case_id='case_a', result_opened=True,
            source_id='source_a', idempotency_key='link')
        client.update_log_sim_beta_task('task_a', project_id='beta', expected_revision=2,
            result_opened=False, idempotency_key='reset-view')
        client.save_log_sim_beta_feedback('task_a', project_id='beta', stage='review', kind='blocked',
            message='Cannot find saved Case', needs_help=True, idempotency_key='feedback')
        client.log_sim_beta_feedback(project_id='beta', task_id='task_a')
        client.log_sim_beta_feedback(project_id='beta')
        assert client.log_sim_beta_overview() == {'id': 'task_a', 'revision': 2}
        assert client.log_sim_beta_overview(format='csv') == 'task_id,stage\ntask_a,review\n'
    assert calls[0].url.path == '/api/v1/log-sim/beta-tasks'
    assert json.loads(calls[0].content) == {'project_id': 'beta', 'pack_id': 'following-gap'}
    assert calls[0].headers['Idempotency-Key'] == 'start'
    assert dict(calls[1].url.params) == {'project_id': 'beta'}
    assert calls[2].url.path.endswith('/beta-tasks/task_a')
    assert calls[3].url.params['revision'] == '2'
    assert calls[3].url.path.endswith('/comparison-settings/settings_a')
    assert calls[4].url.path.endswith('/beta-tasks/task_a/progress')
    assert json.loads(calls[4].content) == {'project_id': 'beta', 'expected_revision': 1,
        'comparison_ref': pin, 'batch_id': 'batch_a', 'case_id': 'case_a', 'result_opened': True, 'source_id': 'source_a'}
    assert calls[4].headers['Idempotency-Key'] == 'link'
    assert json.loads(calls[5].content) == {'project_id': 'beta', 'expected_revision': 2, 'result_opened': False}
    assert calls[6].headers['Idempotency-Key'] == 'feedback'
    assert json.loads(calls[6].content)['needs_help'] is True
    assert calls[7].url.params['task_id'] == 'task_a'
    assert 'task_id' not in calls[8].url.params
    assert calls[9].url.path.endswith('/beta-overview') and 'project_id' not in calls[9].url.params


@pytest.mark.parametrize('invoke', [
    lambda c: c.create_log_sim_beta_task('following-gap', idempotency_key=''),
    lambda c: c.log_sim_beta_task('../other-project'),
    lambda c: c.log_sim_comparison_setting('settings_a', revision=0),
    lambda c: c.update_log_sim_beta_task('task_a', expected_revision=0, idempotency_key='link'),
    lambda c: c.update_log_sim_beta_task('task_a', expected_revision=1, result_opened=1, idempotency_key='link'),
    lambda c: c.save_log_sim_beta_feedback('task_a', stage='review', kind='passed', message='x', idempotency_key='f'),
    lambda c: c.save_log_sim_beta_feedback('task_a', stage='unknown', kind='blocked', message='x', idempotency_key='f'),
    lambda c: c.save_log_sim_beta_feedback('task_a', stage='review', kind='blocked', message=' ', idempotency_key='f'),
    lambda c: c.save_log_sim_beta_feedback('task_a', stage='review', kind='completion', message='x', needs_help=0, idempotency_key='f'),
    lambda c: c.log_sim_beta_overview(format='html'),
])
def test_invalid_guided_request_stays_local(invoke):
    def unexpected(_):
        pytest.fail('Invalid input reached transport')
    with AxiomClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(ValueError):
            invoke(client)
