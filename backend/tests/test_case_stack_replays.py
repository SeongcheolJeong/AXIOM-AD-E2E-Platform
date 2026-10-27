"""Recorded multi-input case workflow; authored geometry, not road performance."""
from copy import deepcopy
import hashlib

import pytest
from sqlalchemy import func, select

from ade2e import case_stack_replays as service
from ade2e.case_models import CaseRevisionRow
from ade2e.job_models import JobRow
from ade2e.recording_models import RecordingMessageRow
from test_stack_replays import context, execute, submit as submit_stack, definition


def setup(client, **kwargs):
    ctx = context(client, **kwargs)
    baseline = execute(ctx, submit_stack(ctx))
    assert baseline.status == 'completed'
    i = baseline.inputs[1].ego
    case = client.post('/api/v1/cases', headers={'Idempotency-Key': 'case-stack-source'}, json={
        'title': 'Investigate this recorded tick', 'note': 'Authored input; no driving claim.',
        'source': {'kind': 'recording', 'recording_id': baseline.recording_id, 'channel_id': i.channel_id,
            'start_time_ns': i.log_time_ns, 'end_time_ns': i.log_time_ns}}).json()
    assert case['segment']['sample_count'] == 1
    body = definition(); body['definition']['policy']['parameters']['time_headway_s'] = 3.0
    candidate = client.post(f'/api/v1/stacks/{baseline.stack_id}/revisions', json={**body, 'expected_revision': 1})
    assert candidate.status_code == 201, candidate.text
    selection = dict(expected_revision=case['revision'], baseline_replay_id=baseline.id,
        stack_id=baseline.stack_id, stack_revision=2, note='Compare unchanged temporal inputs')
    return ctx, baseline, case, selection


def start(client, case, selection, key='start'):
    path = f'/api/v1/cases/{case["id"]}/stack-replays'
    preview = client.post(path + '/preview', json=selection)
    assert preview.status_code == 200, preview.text
    body = {**selection, 'expected_preview_hash': preview.json()['preview_hash'], 'confirm_open_loop': True}
    response = client.post(path, json=body, headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    return response.json(), body, preview.json()


def finish_capture(client, ctx, case):
    plan = case['stack_replays'][0]
    executed = execute(ctx, {'id': plan['job_id']})
    response = client.post(f'/api/v1/cases/{case["id"]}/stack-replays/{plan["id"]}/capture',
        headers={'Idempotency-Key': 'capture'}, json={'expected_revision': case['revision'], 'plan_hash': plan['definition_hash']})
    assert response.status_code == 201, response.text
    return response.json(), executed


def test_real_kernel_full_prefix_and_case_capture_with_identical_source(client):
    ctx, baseline, case, selection = setup(client)
    original = client.get(case['download_url']).content
    original_baseline = client.get(baseline.download_url).content
    launched, body, preview = start(client, case, selection)
    assert preview['workload']['total_ticks'] == 3 and preview['workload']['case_tick_indices'] == [1]
    assert preview['jobs_created'] == 0 and launched['revision'] == 2
    path = f'/api/v1/cases/{case["id"]}/stack-replays'
    assert client.post(path, json=body, headers={'Idempotency-Key': 'start'}).json() == launched
    captured, task = finish_capture(client, ctx, launched)
    assert task.inputs == baseline.inputs and task.status == 'completed'
    report = client.get(path).json(); comparison = report['plans'][0]['comparison']
    assert comparison['status'] == 'comparable' and len(comparison['rows']) == 3
    assert comparison['driving_verdict'] is None and captured['state'] == 'open'
    assert captured['revision'] == 3 and captured['schema_version'].endswith('/5')
    assert client.get(case['download_url']).content == original
    assert client.get(baseline.download_url).content == original_baseline
    exported = client.get(report['download_url']); assert exported.status_code == 200
    assert hashlib.sha256(exported.content).hexdigest() == report['manifest_hash']


@pytest.mark.parametrize('fault', ['preview_hash', 'consent', 'stale_case', 'kernel_revision', 'wrong_kind', 'other_recording'])
def test_invalid_requests_do_not_publish_queue_or_case(client, fault):
    ctx, baseline, case, selection = setup(client)
    path = f'/api/v1/cases/{case["id"]}/stack-replays'
    proposal = client.post(path+'/preview', json=selection).json()
    body = {**selection, 'expected_preview_hash': proposal['preview_hash'], 'confirm_open_loop': True}
    if fault == 'preview_hash': body['expected_preview_hash'] = '0'*64
    if fault == 'consent': body['confirm_open_loop'] = False
    if fault == 'stale_case': body['expected_revision'] = 99
    if fault == 'kernel_revision': body['stack_revision'] = 999
    if fault == 'wrong_kind': body['baseline_replay_id'] = 'job_absent'
    if fault == 'other_recording':
        other = context(client, ego_values={'speed_mps': 4.0}); b = execute(other, submit_stack(other, 'other-baseline'))
        body['baseline_replay_id'] = b.id
    with ctx[1].state.db.session() as session:
        before = session.scalar(select(func.count()).select_from(JobRow))
    result = client.post(path, json=body, headers={'Idempotency-Key': 'invalid'})
    assert result.status_code in (404,409,422), result.text
    with ctx[1].state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(JobRow)) == before
    assert client.get(f'/api/v1/cases/{case["id"]}').json()['revision'] == 1


def test_publication_error_rolls_back_the_job_and_case(client, monkeypatch):
    ctx, baseline, case, selection = setup(client)
    proposal = client.post(f'/api/v1/cases/{case["id"]}/stack-replays/preview',json=selection).json()
    def fail(*args): raise OSError('Injected case publication failure')
    monkeypatch.setattr(service, '_publish', fail)
    with pytest.raises(OSError, match='Injected'):
        client.post(f'/api/v1/cases/{case["id"]}/stack-replays', headers={'Idempotency-Key': 'failed'},
            json={**selection,'expected_preview_hash':proposal['preview_hash'],'confirm_open_loop':True})
    with ctx[1].state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(JobRow)) == 1
        assert session.scalar(select(func.count()).select_from(CaseRevisionRow)) == 1


def test_cancelled_candidate_keeps_all_ticks_and_withholds_deltas(client):
    ctx, _, case, selection = setup(client)
    launched, _, _ = start(client,case,selection); plan=launched['stack_replays'][0]
    path=f'/api/v1/cases/{case["id"]}/stack-replays'
    early=client.post(f'{path}/{plan["id"]}/capture',headers={'Idempotency-Key':'early'},
        json={'expected_revision':2,'plan_hash':plan['definition_hash']})
    assert early.status_code==409
    live=client.get(path).json()
    cancelled=client.post(f'/api/v1/stack-replays/{plan["job_id"]}/cancel')
    assert cancelled.status_code==200,cancelled.text
    assert client.get(live['download_url']).status_code==409
    response=client.post(f'{path}/{plan["id"]}/capture',headers={'Idempotency-Key':'capture-cancelled'},
        json={'expected_revision':2,'plan_hash':plan['definition_hash']})
    assert response.status_code==201,response.text
    report=client.get(path).json()['plans'][0]
    assert report['status']=='cancelled' and report['comparison']['status']=='not_evaluated'
    assert len(report['comparison']['rows'])==3
    assert all(r['control_delta'] is None for r in report['comparison']['rows'])


def test_byte_tampering_blocks_preflight_without_execution(client):
    ctx, baseline, case, selection=setup(client)
    with ctx[1].state.db.session.begin() as session:
        entry=session.get(RecordingMessageRow,(baseline.recording_id,baseline.inputs[0].ego.ordinal))
        entry.sha256='0'*64
    response=client.post(f'/api/v1/cases/{case["id"]}/stack-replays/preview',json=selection)
    assert response.status_code==409,response.text


def test_conditions_and_old_reference_retests_keep_case5(client):
    from test_case_retests import definition as retest_definition
    ctx, baseline, case, selection=setup(client)
    launched,_,_=start(client,case,selection)
    captured,_=finish_capture(client,ctx,launched)
    taxonomy=client.get('/api/v1/case-condition-taxonomy').json()
    response=client.post(f'/api/v1/cases/{case["id"]}/conditions',headers={'Idempotency-Key':'conditions'},json={
        'expected_revision':3,'taxonomy_id':taxonomy['id'],'taxonomy_revision':taxonomy['revision'],
        'taxonomy_hash':taxonomy['manifest_hash'],'values':{},'review_status':'draft',
        'author':'Declared test reviewer','note':'No scene lighting inferred from point data'})
    assert response.status_code==201,response.text
    assert response.json()['schema_version'].endswith('/5') and response.json()['stack_replays']==captured['stack_replays']
    other=client.post(f'/api/v1/cases/{case["id"]}/retests',headers={'Idempotency-Key':'reference-after-stack'},
        json=retest_definition(response.json()))
    assert other.status_code==201,other.text
    assert other.json()['schema_version'].endswith('/5') and other.json()['stack_replays']==captured['stack_replays']
    from ade2e.case_model_replays import _new
    assert _new(other.json())['schema_version'].endswith('/5')


def test_partial_baseline_not_covering_case_is_refused(client):
    ctx, baseline, case, selection=setup(client)
    partial=execute(ctx,submit_stack(ctx,'partial-baseline',body={**ctx[2],'ego_ordinals':ctx[2]['ego_ordinals'][:1]}))
    response=client.post(f'/api/v1/cases/{case["id"]}/stack-replays/preview',json={**selection,'baseline_replay_id':partial.id})
    assert response.status_code==422 and 'exact ego or LiDAR' in response.text


def test_capture_retry_conflict_and_plan_tampering(client):
    ctx, baseline, case, selection=setup(client)
    launched,body,_=start(client,case,selection)
    path=f'/api/v1/cases/{case["id"]}/stack-replays'
    assert client.post(path,headers={'Idempotency-Key':'start'},json={**body,'note':'changed'}).status_code==409
    captured,task=finish_capture(client,ctx,launched);p=launched['stack_replays'][0]
    retry=client.post(f'{path}/{p["id"]}/capture',headers={'Idempotency-Key':'capture'},
        json={'expected_revision':2,'plan_hash':p['definition_hash']})
    assert retry.json()==captured
    with ctx[1].state.db.session.begin() as session:
        row=session.get(CaseRevisionRow,(case['id'],3));row.manifest+=b' '
    assert client.get(path).status_code==409


def test_agent_preview_is_readonly_and_writes_require_keys(client):
    tools=client.get('/api/v1/agent/tools').json()['tools']
    preview=next(t for t in tools if t['name']=='preview_case_stack_replay')
    assert preview['read_only'] and preview['required_headers']==[]
    for name in ('submit_case_stack_replay','capture_case_stack_replay'):
        t=next(t for t in tools if t['name']==name)
        assert not t['read_only'] and t['required_headers']==['Idempotency-Key']


def test_past_case_reports_do_not_acquire_future_attempts(client):
    _, _, case, selection=setup(client)
    start(client,case,selection)
    past=client.get(f'/api/v1/cases/{case["id"]}/stack-replays?revision=1').json()
    assert past['plans']==[]
    client.app.state.settings.api_token='private-test'
    assert client.get(past['download_url']).status_code==401
    assert client.post(f'/api/v1/cases/{case["id"]}/stack-replays/preview',json=selection).status_code==401
