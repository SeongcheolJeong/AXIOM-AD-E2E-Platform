"""Full-source replay orchestration contracts. Runtime fixtures are explicit fakes."""
from copy import deepcopy
import hashlib

import pytest
from sqlalchemy import select, func

from ade2e import jobs, case_model_replays as replay
from ade2e.case_models import CaseRow, CaseRevisionRow
from ade2e.job_models import JobRow
from ade2e.worker import run_once
from test_cases import setup_case
from test_detection_evaluations import context, evaluation_context


def selection(value, result, **changes):
    return dict(expected_revision=value['revision'], baseline_evaluation_id=result.id,
        model_id=result.source_jobs[0]['model_id'], model_revision=result.source_jobs[0]['model_revision'],
        note='Replay the entire baseline split with explicitly selected weights.', **changes)


def started(evaluation_context):
    client, app, _, value, result = setup_case(evaluation_context)
    path = f'/api/v1/cases/{value["id"]}/model-replays'
    request = selection(value, result)
    before = client.get(value['download_url']).content
    response = client.post(path + '/preview', json=request)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['jobs_created'] == 0 and preview['workload']['full_split_images'] == 3
    body = {**request, 'expected_preview_hash': preview['preview_hash'], 'confirm_trusted_model': True}
    response = client.post(path, json=body, headers={'Idempotency-Key': 'model-start'})
    assert response.status_code == 202, response.text
    current = response.json()
    assert client.get(value['download_url']).content == before
    return client, app, current, current['model_replays'][0], body, path


def finish_task(app, task_id):
    return run_once(app.state.db, app.state.registry, 'case-model-test', job_id=task_id,
        runtime_registry=app.state.runtime_registry, sensor_registry=app.state.sensor_registry,
        evaluator_registry=app.state.evaluator_registry)


def action(value, plan):
    return {'expected_revision': value['revision'], 'plan_hash': plan['definition_hash']}


def test_full_split_replay_evaluation_capture_preserves_case_and_baseline(evaluation_context):
    client, app, value, plan, body, path = started(evaluation_context)
    repeated = client.post(path, json=body, headers={'Idempotency-Key': 'model-start'})
    assert repeated.json() == value
    task = finish_task(app, plan['inference']['job_id'])
    assert task.status == 'completed' and task.counts.total == 3
    response = client.post(path + f'/{plan["id"]}/evaluate', json=action(value, plan), headers={'Idempotency-Key': 'evaluate'})
    assert response.status_code == 202, response.text
    next_value = response.json(); updated = next_value['model_replays'][0]
    outcome = finish_task(app, updated['evaluation']['job_id'])
    assert outcome.report.coverage.total == 3 and outcome.report.coverage.complete
    response = client.post(path + f'/{plan["id"]}/capture', json=action(next_value, plan), headers={'Idempotency-Key': 'capture'})
    assert response.status_code == 201, response.text
    captured = response.json()
    assert captured['revision'] == 4 and captured['state'] == 'open'
    assert len(captured['evaluations']) == 2
    report = client.get(path).json()
    assert report['plans'][0]['comparison']['status'] == 'comparable'
    assert report['plans'][0]['comparison']['row_count'] == 3
    assert len(report['plans'][0]['definition']['case_image_ids']) == 1
    raw = client.get(report['download_url']).content
    assert hashlib.sha256(raw).hexdigest() == report['manifest_hash']
    assert client.get(path).json()['manifest_hash'] == report['manifest_hash']


def test_preview_and_stale_request_do_not_publish_work(evaluation_context):
    client, app, _, value, result = setup_case(evaluation_context)
    path = f'/api/v1/cases/{value["id"]}/model-replays'
    with app.state.db.session() as session:
        count = session.scalar(select(func.count()).select_from(JobRow))
    proposal = client.post(path + '/preview', json=selection(value, result)).json()
    response = client.post(path, json={**selection(value, result), 'expected_preview_hash': '0'*64,
        'confirm_trusted_model': True}, headers={'Idempotency-Key': 'stale'})
    assert response.status_code == 409
    with app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(JobRow)) == count
    assert proposal['jobs_created'] == 0


def test_partial_failure_never_fills_predictions_from_baseline(evaluation_context):
    client, app, value, plan, _, path = started(evaluation_context)
    runtime = evaluation_context[0][2]
    runtime.failure_at = runtime.calls + 2
    result = finish_task(app, plan['inference']['job_id'])
    assert result.status == 'failed'
    response = client.post(path + f'/{plan["id"]}/evaluate', json=action(value, plan), headers={'Idempotency-Key': 'failed-eval'})
    assert response.status_code == 202, response.text
    current = response.json()
    ev = finish_task(app, current['model_replays'][0]['evaluation']['job_id'])
    assert ev.report.metrics is None and ev.report.coverage.total == 3
    assert not ev.report.coverage.complete
    response = client.post(path + f'/{plan["id"]}/capture', json=action(current, plan), headers={'Idempotency-Key': 'failed-capture'})
    assert response.status_code == 201, response.text
    report = client.get(path).json()
    assert report['plans'][0]['comparison']['status'] != 'comparable'
    assert all(m['delta'] is None for m in report['plans'][0]['comparison']['metrics'])


def test_queue_case_link_is_one_transaction(evaluation_context, monkeypatch):
    client, app, _, value, result = setup_case(evaluation_context)
    path = f'/api/v1/cases/{value["id"]}/model-replays'
    request = selection(value, result)
    p = client.post(path + '/preview', json=request).json()
    with app.state.db.session() as session:
        count = session.scalar(select(func.count()).select_from(JobRow))
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs): raise OSError('Injected case publication failure after queued job')
        patch.setattr(replay, '_publish', fail)
        with pytest.raises(OSError):
            client.post(path, json={**request, 'expected_preview_hash': p['preview_hash'], 'confirm_trusted_model': True},
                headers={'Idempotency-Key': 'rollback'})
    with app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(JobRow)) == count
        assert session.get(CaseRow, value['id']).revision == 1


def test_boundaries_no_early_evaluation_duplicate_or_wrong_plan(evaluation_context):
    client, app, value, plan, body, path = started(evaluation_context)
    stage = path + f'/{plan["id"]}/evaluate'
    assert client.post(stage, json=action(value, plan), headers={'Idempotency-Key': 'too-early'}).status_code == 409
    assert client.post(stage, json={**action(value, plan), 'plan_hash': '0'*64}, headers={'Idempotency-Key': 'bad-plan'}).status_code == 409
    assert client.post(path, json={**body, 'note': 'Changed request'}, headers={'Idempotency-Key': 'model-start'}).status_code == 409
    assert client.post(path, json={**body, 'confirm_trusted_model': False}, headers={'Idempotency-Key': 'untrusted'}).status_code == 422
    finish_task(app, plan['inference']['job_id'])
    first = client.post(stage, json=action(value, plan), headers={'Idempotency-Key': 'evaluate'})
    assert first.status_code == 202, first.text
    assert client.post(stage, json=action(value, plan), headers={'Idempotency-Key': 'evaluate'}).json() == first.json()
    assert client.post(stage, json=action(first.json(), plan), headers={'Idempotency-Key': 'duplicate-new-key'}).status_code == 409


def test_live_report_export_refuses_changed_state_and_preserves_auth(evaluation_context):
    client, app, value, plan, _, path = started(evaluation_context)
    old = client.get(path).json()
    finish_task(app, plan['inference']['job_id'])
    assert client.get(old['download_url']).status_code == 409
    app.state.settings.api_token = 'private-contract-token'
    assert client.get(path).status_code == 401
    assert client.get(path, headers={'Authorization': 'Bearer private-contract-token'}).status_code == 200


def test_condition_and_regular_revisions_preserve_new_and_old_replay_types(evaluation_context):
    client, app, value, plan, _, path = started(evaluation_context)
    taxonomy = client.get('/api/v1/case-condition-taxonomy').json()
    original = client.get(value['download_url']).content
    response = client.post(f'/api/v1/cases/{value["id"]}/conditions', headers={'Idempotency-Key': 'conditions-after-replay'},
        json={'expected_revision': value['revision'], 'taxonomy_id': taxonomy['id'], 'taxonomy_revision': taxonomy['revision'],
            'taxonomy_hash': taxonomy['manifest_hash'], 'values': {'lighting': 'night'}, 'author': 'Declared test',
            'review_status': 'draft', 'note': 'Not measured conditions'})
    assert response.status_code == 201, response.text
    assert response.json()['schema_version'] == 'axiom-investigation-case/4'
    assert response.json()['model_replays'] == value['model_replays']
    assert client.get(value['download_url']).content == original


def test_cancelled_inference_retains_full_unevaluated_denominator(evaluation_context):
    client, app, value, plan, _, path = started(evaluation_context)
    cancelled = client.post(f'/api/v1/perception/jobs/{plan["inference"]["job_id"]}/cancel')
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    response = client.post(path + f'/{plan["id"]}/evaluate', json=action(value, plan), headers={'Idempotency-Key': 'cancelled-eval'})
    assert response.status_code == 202, response.text
    current = response.json()
    result = finish_task(app, current['model_replays'][0]['evaluation']['job_id'])
    assert result.report.coverage.total == 3 and result.report.coverage.evaluated == 0
    assert result.report.metrics is None


@pytest.mark.parametrize('kind', ['unlinked', 'bounds', 'runtime', 'unreviewed'])
def test_unsupported_baseline_or_runtime_does_not_silently_fallback(evaluation_context, monkeypatch, kind):
    from test_detection_evaluations import execute, submit, revise
    client, app, _, value, result = setup_case(evaluation_context)
    path = f'/api/v1/cases/{value["id"]}/model-replays/preview'
    if kind == 'bounds':
        monkeypatch.setattr(replay, 'MAX_IMAGES', 1)
    if kind == 'runtime':
        evaluation_context[0][2].version = 'changed-runtime-version'
    if kind == 'unlinked':
        result = execute(evaluation_context, submit(evaluation_context, key='unlinked-evaluation'))
    if kind == 'unreviewed':
        dataset = revise(evaluation_context, lambda v: v['images'][0].update(review_status='unreviewed'))
        result = execute(evaluation_context, submit(evaluation_context, key='draft-evaluation',
            body=evaluation_context[-1].model_copy(update={'dataset_revision': dataset['revision']})))
        response = client.post('/api/v1/cases', headers={'Idempotency-Key': 'draft-case'}, json={'title': 'Draft source', 'note': 'Not valid baseline',
            'source': {'kind': 'evaluation', 'evaluation_id': result.id, 'image_id': result.report.rows[0].image_id}})
        value = response.json(); path = f'/api/v1/cases/{value["id"]}/model-replays/preview'
    with app.state.db.session() as session:
        before = session.scalar(select(func.count()).select_from(JobRow))
    assert client.post(path, json=selection(value, result)).status_code in (413, 422)
    with app.state.db.session() as session:
        assert session.scalar(select(func.count()).select_from(JobRow)) == before


def test_agent_catalog_marks_preview_read_only_and_execution_keyed(evaluation_context):
    client, *_ = evaluation_context[0]
    catalog = {t['name']: t for t in client.get('/api/v1/agent/tools').json()['tools']}
    assert catalog['preview_case_model_replay']['read_only'] is True
    assert catalog['preview_case_model_replay']['required_headers'] == []
    for name in ('submit_case_model_replay', 'evaluate_case_model_replay', 'capture_case_model_replay'):
        assert catalog[name]['read_only'] is False
        assert catalog[name]['required_headers'] == ['Idempotency-Key']


@pytest.mark.parametrize('kind', ['payload', 'plan'])
def test_tampering_and_source_corruption_are_not_silently_replayed(evaluation_context, kind):
    client, app, _, value, result = setup_case(evaluation_context)
    path = f'/api/v1/cases/{value["id"]}/model-replays'
    if kind == 'payload':
        from ade2e.recording_models import RecordingRow
        with app.state.db.session() as session:
            row = session.get(RecordingRow, value['segment']['recording_id'])
            file = app.state.db.recording_dir / row.index_storage_key
        file.write_bytes(b'corrupted input')
        assert client.post(path + '/preview', json=selection(value, result)).status_code == 409
    else:
        p = client.post(path + '/preview', json=selection(value, result)).json()
        saved = client.post(path, json={**selection(value, result), 'expected_preview_hash': p['preview_hash'],
            'confirm_trusted_model': True}, headers={'Idempotency-Key': 'start'}).json()
        with app.state.db.session.begin() as session:
            row = session.get(CaseRevisionRow, (saved['id'], saved['revision']))
            row.manifest += b' '
        assert client.get(path).status_code == 409
