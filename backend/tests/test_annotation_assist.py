"""Label-assist transactions and lineage. Fake inference is not an accuracy claim."""
from copy import deepcopy
import hashlib

import pytest
from sqlalchemy import select, func

from ade2e import annotation_assist as assist
from ade2e.annotation_models import AnnotationTaskRow, AnnotationPublicationRow
from ade2e.dataset_models import DatasetRevisionRow
from ade2e.job_models import JobRow
from ade2e.service import digest
from test_detection_evaluations import context, evaluation_context, execute as execute_eval
from test_perception_jobs import execute


@pytest.fixture
def setup(evaluation_context):
    ctx, _, source_dataset, _, evaluation = evaluation_context
    client, app, runtime, request = ctx
    members = deepcopy(source_dataset['images'])
    for member in members[:2]:
        member.update(annotations=[], review_status='unreviewed', reviewer='', review_note='', annotation_source='unknown')
    body = {'name': 'Label-assist target', 'labels': source_dataset['labels'], 'note': 'Synthetic target', 'images': members}
    r = client.post('/api/v1/datasets', json=body); assert r.status_code == 201, r.text
    dataset = r.json()
    selection = {'expected_revision': 1, 'model_id': request.model_id, 'model_revision': 1, 'runtime_id': 'test-inference',
        'class_mapping': [{'model_label': 'test-object', 'dataset_label': 'target'}], 'note': 'Draft only, never independent truth'}
    return ctx, dataset, selection, evaluation_context


def start(setup, *, changes=None, key='submit'):
    (client, *_), dataset, selection, _ = setup
    selection = {**selection, **(changes or {})}
    path = f'/api/v1/datasets/{dataset["id"]}/annotation-tasks'
    preview = client.post(path+'/preview', json=selection)
    assert preview.status_code == 200, preview.text
    body = {**selection, 'expected_preview_hash': preview.json()['preview_hash'], 'confirm_trusted_model': True}
    task = client.post(path, json=body, headers={'Idempotency-Key': key})
    assert task.status_code == 202, task.text
    return task.json(), body, preview.json()


def finish(setup, task):
    for job in task['jobs']: execute(setup[0], job)
    r = setup[0][0].get('/api/v1/annotation-tasks/'+task['id'])
    assert r.status_code == 200, r.text
    return r.json()


def publish(setup, task, *, expected_revision=1):
    client = setup[0][0]; report = client.get('/api/v1/annotation-tasks/'+task['id']).json()
    body = {'expected_revision': expected_revision, 'expected_report_hash': report['manifest_hash'], 'note': 'Publish model drafts for human review'}
    r = client.post(f'/api/v1/annotation-tasks/{task["id"]}/publish', json=body, headers={'Idempotency-Key': 'publish'})
    return r, body


def job_count(app):
    with app.state.db.read_session() as s: return s.scalar(select(func.count()).select_from(JobRow))


def test_preview_and_publish_preserve_reviewed_and_original_snapshots(setup):
    (client, app, runtime, _), dataset, selection, _ = setup
    original = client.get(dataset['download_url']).content
    before = job_count(app); calls = runtime.calls
    p = client.post(f'/api/v1/datasets/{dataset["id"]}/annotation-tasks/preview', json=selection).json()
    assert p['jobs_created'] == 0 and job_count(app) == before and runtime.calls == calls
    assert p['workload']['target_images'] == 2 and p['workload']['preserved_images'] == 1
    task, body, _ = start(setup)
    retry = client.post(f'/api/v1/datasets/{dataset["id"]}/annotation-tasks', json=body, headers={'Idempotency-Key': 'submit'})
    assert retry.json() == task and job_count(app) == before+1
    report = finish(setup, task)
    assert report['counts']['suggested'] == 2
    assert hashlib.sha256(client.get(report['download_url']).content).hexdigest() == report['manifest_hash']
    result, body = publish(setup, task)
    assert result.status_code == 201, result.text
    receipt = result.json()
    out = client.get(f'/api/v1/datasets/{dataset["id"]}?revision=2').json()
    assert out['stats']['unreviewed'] == 2 and out['stats']['reviewed'] == 1
    assert out['images'][2] == dataset['images'][2]
    assert all(m['review_status'] == 'unreviewed' and m['annotation_source'] == 'imported' for m in out['images'][:2])
    assert receipt['reviewed_by_publication'] == 0
    assert client.get(dataset['download_url']).content == original
    assert client.post(f'/api/v1/annotation-tasks/{task["id"]}/publish', json=body, headers={'Idempotency-Key': 'publish'}).json() == receipt
    lineage = client.get(f'/api/v1/datasets/{dataset["id"]}/annotation-lineage?revision=2').json()
    assert len(lineage['images']) == 2 and lineage['independence_verified'] is False


@pytest.mark.parametrize('fault', ['hash', 'consent', 'stale', 'mapping', 'wrong_model', 'empty_mapping'])
def test_rejected_requests_do_not_create_jobs_or_tasks(setup, fault):
    (client, app, *_), dataset, selection, _ = setup
    path = f'/api/v1/datasets/{dataset["id"]}/annotation-tasks'
    p = client.post(path+'/preview', json=selection).json()
    body = {**selection, 'expected_preview_hash': p['preview_hash'], 'confirm_trusted_model': True}
    if fault == 'hash': body['expected_preview_hash'] = '0'*64
    if fault == 'consent': body['confirm_trusted_model'] = False
    if fault == 'stale': body['expected_revision'] = 9
    if fault == 'mapping': body['class_mapping'][0]['dataset_label'] = 'unlisted'
    if fault == 'empty_mapping': body['class_mapping'][0]['dataset_label'] = None
    if fault == 'wrong_model': body['model_revision'] = 999
    before = job_count(app)
    assert client.post(path, json=body, headers={'Idempotency-Key': 'invalid'}).status_code in (404, 409, 422)
    assert job_count(app) == before
    assert client.get(path).json()['items'] == []


def test_zero_predictions_stay_unreviewed_not_negative(setup):
    task, *_ = start(setup, changes={'score_threshold': .99})
    r = finish(setup, task); assert r['counts']['empty_prediction'] == 2
    out, _ = publish(setup, task); assert out.status_code == 201, out.text
    dataset = setup[0][0].get(f'/api/v1/datasets/{setup[1]["id"]}').json()
    assert dataset['stats']['negative_images'] == 1  # Only the original human-declared negative.
    assert dataset['stats']['unreviewed'] == 2 and all(not m['annotations'] for m in dataset['images'][:2])


def test_cancelled_task_preserves_all_rows_and_does_not_publish_fake_labels(setup):
    task, *_ = start(setup)
    client = setup[0][0]
    for job in task['jobs']: assert client.post(f'/api/v1/perception/jobs/{job["id"]}/cancel').status_code == 200
    r = client.get('/api/v1/annotation-tasks/'+task['id']).json()
    assert r['terminal'] and len(r['rows']) == 2 and r['counts']['not_attempted'] == 2
    assert publish(setup, task)[0].status_code == 409


def test_partial_failure_is_not_a_negative_and_existing_labels_are_retained(setup):
    task, *_ = start(setup)
    runtime = setup[0][2]; runtime.failure_at = runtime.calls+2
    r = finish(setup, task)
    assert r['terminal'] and r['counts']['suggested'] == 1 and r['counts']['failed'] == 1
    out, _ = publish(setup, task); assert out.status_code == 201, out.text
    current = setup[0][0].get(f'/api/v1/datasets/{setup[1]["id"]}').json()
    assert len(current['images']) == 3 and current['images'][1] == setup[1]['images'][1]


def test_stale_dataset_refuses_rebase_and_future_publication(setup):
    task, *_ = start(setup); finish(setup, task)
    client = setup[0][0]; old = setup[1]
    fields = ('name', 'description', 'labels', 'source', 'source_revision', 'license', 'note', 'images')
    body = {k: old[k] for k in fields}; body.update(expected_revision=1, note='Other user edit')
    assert client.post(f'/api/v1/datasets/{old["id"]}/revisions', json=body).status_code == 201
    assert publish(setup, task, expected_revision=2)[0].status_code == 409


def test_publication_transaction_rolls_back_dataset_and_receipt(setup, monkeypatch):
    task, *_ = start(setup); finish(setup, task)
    original = assist._insert_revision
    def fail(*a, **kw): original(*a, **kw); raise RuntimeError('Injected publication failure')
    monkeypatch.setattr(assist, '_insert_revision', fail)
    with pytest.raises(RuntimeError, match='Injected'): publish(setup, task)
    client, app, *_ = setup[0]
    assert client.get('/api/v1/datasets/'+setup[1]['id']).json()['revision'] == 1
    with app.state.db.read_session() as s:
        assert s.get(AnnotationPublicationRow, task['id']) is None
        assert s.get(DatasetRevisionRow, (setup[1]['id'], 2)) is None


def test_submission_transaction_rolls_back_jobs_after_failed_link(setup, monkeypatch):
    before = job_count(setup[0][1])
    def fail(*_): raise RuntimeError('Injected link failure')
    monkeypatch.setattr(assist, 'task_get', fail)
    with pytest.raises(RuntimeError, match='Injected'): start(setup)
    assert job_count(setup[0][1]) == before


def test_review_retains_origin_and_rejects_same_generator_model(setup):
    task, *_ = start(setup); finish(setup, task); assert publish(setup, task)[0].status_code == 201
    client = setup[0][0]; d = client.get('/api/v1/datasets/'+setup[1]['id']).json()
    fields = ('name', 'description', 'labels', 'source', 'source_revision', 'license', 'note', 'images')
    body = {k: d[k] for k in fields}; body.update(expected_revision=2, note='Human review, not independent benchmark')
    for m in body['images']:
        m.update(review_status='reviewed', reviewer='Declared human', review_note='Checked original image')
    assert client.post(f'/api/v1/datasets/{d["id"]}/revisions', json=body).status_code == 201
    request = setup[3][4].model_dump(mode='json')
    request.update(dataset_id=d['id'], dataset_revision=3, inference_job_ids=[task['jobs'][0]['id']])
    r = client.post('/api/v1/detection-evaluations', json=request, headers={'Idempotency-Key': 'self-score'})
    assert r.status_code == 422 and 'Self-evaluation blocked' in r.text
    current = client.get(f'/api/v1/datasets/{d["id"]}/annotation-lineage?revision=3').json()
    assert len(current['images']) == 2
    assert not client.get(f'/api/v1/datasets/{d["id"]}/annotation-lineage?revision=1').json()['images']


def test_collection_label_copy_retains_model_origins(setup):
    task, *_ = start(setup); finish(setup, task); assert publish(setup, task)[0].status_code == 201
    client = setup[0][0]; d = client.get('/api/v1/datasets/'+setup[1]['id']).json()
    selection = {'name': 'Copied labels', 'purpose': 'Keep model origin', 'sources': [
        {'kind': 'dataset', 'dataset_id': d['id'], 'revision': 2, 'reason': 'Source labels'}]}
    p = client.post('/api/v1/collections/preview', json=selection).json()
    c = client.post('/api/v1/collections', json={**selection,'expected_preview_hash':p['preview_hash']}, headers={'Idempotency-Key':'copy'}).json()
    body = {'collection_revision': 1, 'name':'Copy','note':'Preserve origin','labels':d['labels'], 'decisions': [
        {'key': r['key'], 'action':'include','image_id': r['images'][0]['id'],
         'label_option_id':r['label_options'][0]['id'], 'split':'test'} for r in c['selection']['rows']]}
    p = client.post(f'/api/v1/collections/{c["id"]}/datasets/preview', json=body).json()
    out = client.post(f'/api/v1/collections/{c["id"]}/datasets', json={**body,'expected_preview_hash':p['preview_hash']}, headers={'Idempotency-Key':'copy-dataset'})
    assert out.status_code == 201, out.text
    origins = client.get(f'/api/v1/datasets/{out.json()["dataset"]["id"]}/annotation-lineage').json()['images']
    assert len(origins) == 2


def test_changed_manifest_is_not_exported(setup):
    task, *_ = start(setup)
    with setup[0][1].state.db.session.begin() as s:
        row = s.get(AnnotationTaskRow, task['id']); row.manifest += b' '
    assert setup[0][0].get('/api/v1/annotation-tasks/'+task['id']).status_code == 409


def test_nonempty_human_drafts_and_reviewed_images_are_not_selected(setup):
    client = setup[0][0]; d = setup[1]
    fields = ('name', 'description', 'labels', 'source', 'source_revision', 'license', 'note', 'images')
    body = {k: deepcopy(d[k]) for k in fields}; body.update(expected_revision=1)
    body['images'][0]['annotations'] = [{'id':'human','label':'target','bbox_xyxy':[0.,0.,1.,1.]}]
    assert client.post(f'/api/v1/datasets/{d["id"]}/revisions', json=body).status_code == 201
    task, _, p = start(setup, changes={'expected_revision': 2})
    assert p['workload']['target_images'] == 1 and p['workload']['preserved_images'] == 2


def test_multiple_recordings_are_explicit_separate_jobs_with_one_atomic_intent(setup):
    from test_sensors import make_recording
    client = setup[0][0]; d = setup[1]
    recording, channels, _ = make_recording(client, camera_frame='camera_second')
    image = client.post('/api/v1/dataset-images', json={'recording_id': recording['id'], 'channel_id': channels[0],
        'ordinal': 1, 'decoder_id': 'foxglove-json', 'decoder_version': '1'}).json()
    fields = ('name', 'description', 'labels', 'source', 'source_revision', 'license', 'note', 'images')
    body = {k: deepcopy(d[k]) for k in fields}; body.update(expected_revision=1)
    body['images'].append({'image_id': image['id'], 'split': 'test'})
    response = client.post(f'/api/v1/datasets/{d["id"]}/revisions', json=body)
    assert response.status_code == 201, response.text
    task, _, p = start(setup, changes={'expected_revision': 2})
    assert p['workload']['recordings'] == 2 and p['workload']['inference_jobs'] == 2
    r = finish(setup, task)
    assert len(r['rows']) == 3 and r['counts']['suggested'] == 3
    assert publish(setup, task, expected_revision=2)[0].status_code == 201
    assert client.get('/api/v1/datasets/'+d['id']).json()['stats']['images'] == 4


def test_versioned_model_or_source_drift_blocks_submission(setup):
    client, app, runtime, _ = setup[0]; dataset, selection = setup[1:3]
    path = f'/api/v1/datasets/{dataset["id"]}/annotation-tasks'
    p = client.post(path+'/preview', json=selection).json()
    runtime.version = 'unexpected-version'
    before = job_count(app)
    response = client.post(path, json={**selection, 'confirm_trusted_model': True, 'expected_preview_hash': p['preview_hash']},
        headers={'Idempotency-Key': 'drift'})
    assert response.status_code == 409 and job_count(app) == before


def test_payload_budget_rejects_without_silent_subselection(setup, monkeypatch):
    monkeypatch.setattr(assist, 'MAX_BYTES', 1)
    client = setup[0][0]
    response = client.post(f'/api/v1/datasets/{setup[1]["id"]}/annotation-tasks/preview', json=setup[2])
    assert response.status_code == 413


def test_agent_actions_match_explicit_execution_boundaries(client):
    tools = {t['name']: t for t in client.get('/api/v1/agent/tools').json()['tools']}
    assert tools['preview_annotation_task']['read_only'] is True
    assert tools['create_annotation_task']['read_only'] is False
    assert tools['create_annotation_task']['required_headers'] == ['Idempotency-Key']
    assert tools['publish_annotation_drafts']['required_headers'] == ['Idempotency-Key']


def test_unreviewed_evaluation_preserves_model_origin_without_scores(setup):
    task, *_ = start(setup);finish(setup,task);assert publish(setup,task)[0].status_code==201
    client = setup[0][0]
    body = setup[3][4].model_dump(mode='json')
    body.update(dataset_id=setup[1]['id'],dataset_revision=2,inference_job_ids=[task['jobs'][0]['id']])
    response=client.post('/api/v1/detection-evaluations',json=body,headers={'Idempotency-Key':'draft-no-score'})
    assert response.status_code==202,response.text
    value=execute_eval(setup[3],response.json())
    assert value.report.metrics is None and not value.report.coverage.complete
    origin=value.source_context['annotation_lineage']
    assert origin['independence_verified'] is False and len(origin['images'])==2
    assert 'annotation_lineage' in client.get(value.download_url).json()['source_context']
