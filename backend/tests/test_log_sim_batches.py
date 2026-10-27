"""Real reference queue over bounded authored retained logs, not learned-model validation."""
import copy
import pytest
from fastapi import APIRouter, Depends, HTTPException, Request

from ade2e import log_sim, log_sim_batches as batches, log_sim_replays as replay
from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.team_access import authenticate_request
from ade2e.worker import run_once
from test_log_sim_replays import workflow, job_count
from test_log_sim import log_env
from test_object_demo import env


@pytest.fixture
def batch_env(workflow):
    db, client, registry, selection = workflow
    def auth(request: Request):
        authenticate_request(request, db, Settings(api_token='test-bootstrap', team_mode=True,
            seed_examples=False), request.headers.get('authorization', '')[7:])
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    batches.register_log_sim_batch_routes(api, db, registry)
    client.app.include_router(api)
    return db, client, registry, {'source_ids': [selection['source_id']]}


def submit(env, key='batch'):
    db, client, _, body = env
    count = job_count(db)
    p = client.post('/api/v1/log-sim/batches/preview', json=body)
    assert p.status_code == 200, p.text
    assert job_count(db) == count
    response = client.post('/api/v1/log-sim/batches', json=body | {'preview_hash': p.json()['preview_hash']},
        headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    return response.json(), p.json()


def finish(env, batch):
    db, client, registry, _ = env
    for source in batch['sources']:
        for identity in source.get('execution_ids', {}).values():
            assert run_once(db, Registry(), 'batch-reference-worker', job_id=identity, experiment_registry=registry)
    response = client.get('/api/v1/log-sim/batches/'+batch['id'])
    assert response.status_code == 200, response.text
    return response.json()


def test_actual_pair_shared_by_all_ranked_candidates_and_case_local_metrics(batch_env):
    db, client, _, _ = batch_env
    count = job_count(db)
    batch, preview = submit(batch_env)
    assert preview['planned_execution_count'] == 2 and job_count(db) == count+2
    assert preview['profiles']['baseline']['controller']['cruise_speed_mps'] == 8
    assert preview['profiles']['candidate']['controller']['cruise_speed_mps'] == 8
    assert preview['profiles']['candidate']['controller']['time_headway_s'] == 3
    assert batch['candidates'] == []
    done = finish(batch_env, batch)
    assert done['status'] == 'completed'
    assert done['counts']['completed_sources'] == 1
    assert {item['kind'] for item in done['candidates']} >= {'nearby_ahead', 'decision_divergence', 'opposite_acceleration'}
    assert len({item['comparison_id'] for item in done['candidates']}) == 1
    pair = client.get('/api/v1/log-sim/comparisons/'+done['sources'][0]['comparison_id']).json()
    assert pair['case_ref'] is None and pair['case_frame_count'] == 6
    for candidate in done['candidates']:
        samples = pair['result']['samples'][candidate['start_index']:candidate['end_index']+1]
        assert candidate['metrics'] == batches.window_metrics(samples)
    assert done['candidates'][0]['metrics']['decision_difference_ratio'] == 1
    assert client.get('/api/v1/log-sim/comparisons').json()['items'] == []
    assert client.get('/api/v1/log-sim/batches/'+batch['id']).json() == done
    assert client.get('/api/v1/log-sim/batches').json()['items'][0]['id'] == batch['id']


def test_selected_case_bundle_is_owned_pinned_and_idempotent(batch_env):
    _, client, _, _ = batch_env
    batch, _ = submit(batch_env)
    done = finish(batch_env, batch)
    selected = done['candidates'][:2]
    body = {'name': 'Review these requests', 'candidate_ids': [item['id'] for item in selected], 'report_hash': done['report_hash']}
    path = '/api/v1/log-sim/batches/'+done['id']+'/case-bundles'
    result = client.post(path, json=body, headers={'Idempotency-Key': 'bundle'})
    assert result.status_code == 201, result.text
    bundle = result.json()
    assert bundle['status'] == 'completed' and len(bundle['items']) == 2
    assert bundle['review_status'] == 'unreviewed' and bundle['verdict'] == 'not_evaluated'
    assert client.post(path, json=body, headers={'Idempotency-Key': 'bundle'}).json() == bundle
    for item in bundle['items']:
        case = client.get('/api/v1/log-sim/sources/'+item['source_id']+'/cases/'+item['case_id']).json()
        assert case['revision'] == item['case_revision'] and case['manifest_hash'] == item['case_manifest_hash']
        candidate = next(c for c in selected if c['id'] == item['candidate_id'])
        assert case['segment']['sample_count'] == candidate['metrics']['sample_count']
    assert client.get('/api/v1/log-sim/case-bundles').json()['items'][0]['id'] == bundle['id']
    assert client.get('/api/v1/log-sim/case-bundles/'+bundle['id']).json() == bundle
    assert client.post(path, json=body | {'report_hash': '0'*64}, headers={'Idempotency-Key': 'stale'}).status_code == 409
    assert client.post(path, json=body | {'candidate_ids': ['foreign']}, headers={'Idempotency-Key': 'foreign'}).status_code == 422


def test_preview_stale_and_idempotence_do_not_duplicate_queue(batch_env):
    db, client, _, body = batch_env
    count = job_count(db)
    stale = client.post('/api/v1/log-sim/batches', json=body | {'preview_hash': '0'*64}, headers={'Idempotency-Key': 'stale'})
    assert stale.status_code == 409 and job_count(db) == count
    batch, p = submit(batch_env)
    same = client.post('/api/v1/log-sim/batches', json=body | {'preview_hash': p['preview_hash']}, headers={'Idempotency-Key': 'batch'})
    assert same.json() == batch and job_count(db) == count+2
    assert client.post('/api/v1/log-sim/batches', json=body | {'preview_hash': '0'*64}, headers={'Idempotency-Key': 'batch'}).status_code == 409
    assert client.post('/api/v1/log-sim/batches/preview', json={'source_ids': body['source_ids']*2}).status_code == 422


def test_unsupported_source_stays_in_denominator(batch_env, monkeypatch):
    db, client, registry, body = batch_env
    original = log_sim._source
    def source(session, project, identity):
        value = original(session, project, body['source_ids'][0] if identity == 'unsupported-log' else identity)
        if identity == 'unsupported-log':
            value = copy.deepcopy(value)
            value.update(id=identity, source_hash='e'*64, recording_ref=None, title='Unavailable original')
        return value
    monkeypatch.setattr(log_sim, '_source', source)
    multi = (db, client, registry, {'source_ids': body['source_ids']+['unsupported-log']})
    batch, p = submit(multi)
    assert p['counts']['source_count'] == 2 and p['counts']['unsupported_sources'] == 1
    done = finish(multi, batch)
    assert done['counts']['completed_sources'] == 1 and done['counts']['unsupported_sources'] == 1
    assert len(done['sources']) == 2


def test_partial_submission_checkpoint_and_retry_are_idempotent(batch_env, monkeypatch):
    db, client, _, body = batch_env
    original = replay.experiment_jobs.submit
    stopped = False
    def fail_candidate(*args):
        nonlocal stopped
        if args[-1].endswith(':candidate') and not stopped:
            stopped = True
            raise HTTPException(503, 'Temporary fixture worker unavailability')
        return original(*args)
    monkeypatch.setattr(replay.experiment_jobs, 'submit', fail_candidate)
    count = job_count(db)
    batch, p = submit(batch_env)
    assert batch['sources'][0]['status'] == 'submission_failed' and job_count(db) == count+1
    first_job = batch['sources'][0]['execution_ids']['baseline']
    resumed = client.post('/api/v1/log-sim/batches', json=body | {'preview_hash': p['preview_hash']}, headers={'Idempotency-Key': 'batch'})
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()['sources'][0]['execution_ids']['baseline'] == first_job
    assert job_count(db) == count+2
    assert finish(batch_env, resumed.json())['status'] == 'completed'


def test_cancelled_pair_is_unevaluated_and_explicit_retry_preserves_attempt(batch_env):
    db, client, _, _ = batch_env
    batch, _ = submit(batch_env)
    candidate_job = batch['sources'][0]['execution_ids']['candidate']
    assert client.post('/api/v1/experiments/executions/'+candidate_job+'/cancel').status_code == 200
    before = client.get('/api/v1/log-sim/batches/'+batch['id']).json()
    assert before['status'] == 'completed_with_errors' and before['candidates'] == []
    assert before['counts']['failed_sources'] == 1
    url = '/api/v1/log-sim/batches/'+batch['id']+'/retry'
    count = job_count(db)
    retry = client.post(url, json={}, headers={'Idempotency-Key': 'retry'})
    assert retry.status_code == 202, retry.text
    assert retry.json()['sources'][0]['attempts'] == [batch['sources'][0]['comparison_id']]
    assert job_count(db) == count+2
    again = client.post(url, json={}, headers={'Idempotency-Key': 'retry'})
    assert again.json() == retry.json() and job_count(db) == count+2
    assert finish(batch_env, retry.json())['status'] == 'completed'


def test_project_roles_and_legacy_case_boundary(batch_env):
    _, client, _, body = batch_env
    other = client.post('/api/v1/projects', json={'name': 'Other'}).json()['id']
    def headers(role, project='default'):
        token = client.post('/api/v1/projects/'+project+'/tokens', json={'user_label': role, 'role': role}).json()['token']
        return {'Authorization': 'Bearer '+token, 'Idempotency-Key': role}
    viewer, engineer, foreign = headers('Viewer'), headers('Engineer'), headers('Engineer', other)
    p = client.post('/api/v1/log-sim/batches/preview', json=body, headers=viewer)
    assert p.status_code == 200
    payload = body | {'preview_hash': p.json()['preview_hash']}
    assert client.post('/api/v1/log-sim/batches', json=payload, headers=viewer).status_code == 403
    result = client.post('/api/v1/log-sim/batches', json=payload, headers=engineer)
    assert result.status_code == 202, result.text
    path = '/api/v1/log-sim/batches/'+result.json()['id']
    assert client.get(path, headers=viewer).status_code == 200
    assert client.get(path, headers=foreign).status_code == 404
    assert client.get(path+'?project_id='+other, headers=foreign).status_code == 404
    assert client.post(path+'/retry', json={}, headers=viewer).status_code == 403
    assert client.get('/api/v1/cases', headers=engineer).status_code == 403


def test_completed_uncertainty_has_no_ranked_candidate(batch_env, monkeypatch):
    _, client, _, _ = batch_env
    batch, _ = submit(batch_env)
    finish(batch_env, batch)
    trace = replay._trace
    def uncertain(*args):
        detail, samples = trace(*args)
        detail['outcome_uncertain'] = True
        return detail, samples
    monkeypatch.setattr(replay, '_trace', uncertain)
    report = client.get('/api/v1/log-sim/batches/'+batch['id']).json()
    assert report['sources'][0]['status'] == 'not_evaluated' and report['candidates'] == []
    assert report['counts']['failed_sources'] == 1


def test_library_keeps_unavailable_scenes_and_rejects_unpinned_selection(batch_env, monkeypatch):
    from ade2e import log_sim_library
    db, client, registry, body = batch_env
    source_id = body['source_ids'][0]
    library = {'manifest_hash': 'a'*64, 'items': [
        {'id': 'original_scene_a', 'segments': [{'source_id': source_id}]},
        {'id': 'original_scene_b', 'segments': []},
    ]}
    monkeypatch.setattr(log_sim_library, 'library_snapshot', lambda *args: library)
    selection = body | {'library_id': 'loglib_test', 'selected_scene_ids': ['original_scene_a', 'original_scene_b']}
    batch, p = submit((db, client, registry, selection))
    assert p['counts']['distinct_scenes'] == 2 and p['counts']['source_count'] == 1
    assert p['counts']['unavailable_scene_count'] == 1
    done = finish(batch_env, batch)
    assert done['counts']['distinct_scenes'] == 2 and done['counts']['unavailable_scene_count'] == 1
    assert client.post('/api/v1/log-sim/batches/preview', json=selection | {'selected_scene_ids': ['foreign_scene']}).status_code == 422
    library['items'][0]['segments'].append({'source_id': 'missing_interval'})
    assert client.post('/api/v1/log-sim/batches/preview', json=selection).status_code == 422


def test_source_grant_denial_applies_before_preview_and_saved_report(batch_env, monkeypatch):
    db, client, _, body = batch_env
    batch, _ = submit(batch_env)
    original = log_sim._source
    def road(session, project, identity):
        return original(session, project, identity) | {'kind': 'nuscenes'}
    monkeypatch.setattr(log_sim, '_source', road)
    def denied(*args):
        raise HTTPException(404, 'Granted project recording source not found')
    monkeypatch.setattr(batches, 'authorize_recording_source', denied)
    before = job_count(db)
    assert client.post('/api/v1/log-sim/batches/preview', json=body).status_code == 404
    assert job_count(db) == before
    with db.session.begin() as session:
        value = replay._get(session, 'default', batches.BATCH, batch['id'])
        value['sources'][0]['source_kind'] = 'nuscenes'
        replay._put(session, 'default', batches.BATCH, batch['id'], value)
    assert client.get('/api/v1/log-sim/batches/'+batch['id']).status_code == 404
    assert client.get('/api/v1/log-sim/batches').status_code == 404
