"""Durable batch contracts using authored handlers, never native CARLA/SSH."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import threading
import pytest
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from ade2e import experiment_jobs, jobs
from ade2e.adapters import AdapterCancelled, Registry
from ade2e.database import Settings
from ade2e.experiment_models import ExperimentArtifactRow, ExperimentRow
from ade2e.hil_batches import advance, register_hil_batch_routes
from ade2e.hil_demo import ADAPTER
from ade2e.job_models import EngineResourceRow, JobRow
from ade2e.team_access import authenticate_request
from ade2e.worker import run_once
from test_hil_beta import env as hil_env, base_env, body, save, ref
from test_hil_dut_profiles import pair


@pytest.fixture
def env(hil_env, tmp_path):
    db, client, _, registry, _ = hil_env
    settings = Settings(data_dir=tmp_path, api_token='test-bootstrap', team_mode=True, seed_examples=False)
    def authenticate(request: Request):
        header = request.headers.get('authorization', '')
        authenticate_request(request, db, settings, header[7:] if header.startswith('Bearer ') else None)
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(authenticate)])
    register_hil_batch_routes(api, db, registry)
    client.app.include_router(api)
    yield hil_env


def batch(env, tests=None, key='batch'):
    tests = tests or [save(env, {**body(env), 'name': f'Condition {index}', 'parameters': {'delay_ms': delay}}, f'test-{index}')
                      for index, delay in enumerate((200, 300, 400))]
    response = env[1].post('/api/v1/hil-sim/batches', json=dict(project_id='default', name='Age sweep', note='Authored fixture only',
                          tests=[ref(test) for test in tests]), headers={'Idempotency-Key': key})
    assert response.status_code == 201, response.text
    return response.json()


def get(env, value):
    response = env[1].get('/api/v1/hil-sim/batches/' + value['id'])
    assert response.status_code == 200, response.text
    return response.json()


def preview(env, value):
    return env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/preview',
                      json=dict(project_id='default', expected_revision=value['revision']))


def submit(env, value, action='start', key=None):
    review = preview(env, value)
    assert review.status_code == 200, review.text
    payload = dict(project_id='default', expected_revision=value['revision'], preview_hash=review.json()['preview_hash'])
    response = env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/' + action, json=payload,
                          headers={'Idempotency-Key': key or action})
    assert response.status_code == 202, response.text
    return response.json(), payload


def cancel(env, value, key='cancel'):
    response = env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/cancel',
        json=dict(project_id='default', expected_revision=value['revision']), headers={'Idempotency-Key': key})
    assert response.status_code == 200, response.text
    return response.json()


def count(env, model=JobRow):
    with env[0].read_session() as session:
        return session.scalar(select(func.count()).select_from(model))


def work(env):
    return run_once(env[0], Registry(), 'batch-authored-worker', experiment_registry=env[3])


def test_offline_save_exact_revisions_profiles_and_no_get_dispatch(env):
    profiles = pair(env)
    first = save(env, {**body(env), 'dut_profiles': {side: ref(value) for side, value in profiles.items()}}, 'profile-test')
    second = save(env, {**body(env), 'id': first['id'], 'expected_revision': 1, 'parameters': {'delay_ms': 400}}, 'test-r2')
    third = save(env, {**body(env), 'parameters': {'delay_ms': 500}}, 'third')
    handler = env[3]._handlers.pop(ADAPTER)
    value = batch(env, [first, second, third])
    assert value['items'][0]['test']['dut_profiles'] == profiles
    assert value['items'][0]['test_ref']['id'] == value['items'][1]['test_ref']['id']
    assert value['definition_sha256'] and value['status'] == 'draft' and value['can_start']
    assert get(env, value) == value and count(env) == 0 and handler.probes == 0
    assert preview(env, value).status_code == 422
    assert batch(env, [first, second, third]) == value
    env[3]._handlers[ADAPTER] = handler
    running, _ = submit(env, value)
    for _ in range(3):
        assert get(env, running)['status'] == 'running'
    assert count(env) == 0  # GET and start acceptance never enqueue an item.


@pytest.mark.parametrize('length', [0, 2, 6])
def test_small_batch_bounds(env, length):
    test = save(env)
    response = env[1].post('/api/v1/hil-sim/batches', json=dict(name='Invalid', tests=[ref(test)] * length),
                          headers={'Idempotency-Key': 'invalid'})
    assert response.status_code == 422


def test_duplicate_hash_acl_and_agent_role(env):
    value = batch(env)
    refs = [item['test_ref'] for item in value['items']]
    for selected, code in (([refs[0]] * 3, 422), ([{**refs[0], 'sha256': '0' * 64}, *refs[1:]], 409)):
        response = env[1].post('/api/v1/hil-sim/batches', json=dict(name='Invalid', tests=selected),
                              headers={'Idempotency-Key': 'invalid'})
        assert response.status_code == code
    project = env[1].post('/api/v1/projects', json={'name': 'Another project'}).json()
    viewer = env[1].post('/api/v1/projects/default/tokens', json={'role': 'Viewer', 'user_label': 'Batch viewer'}).json()['token']
    engineer = env[1].post('/api/v1/projects/' + project['id'] + '/tokens', json={'role': 'Engineer', 'user_label': 'Other engineer'}).json()['token']
    headers = {'Authorization': 'Bearer ' + viewer, 'Idempotency-Key': 'viewer'}
    assert env[1].get('/api/v1/hil-sim/batches', headers=headers).status_code == 200
    assert env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/preview',
                      json={'expected_revision': 1}, headers=headers).status_code == 403
    assert env[1].get('/api/v1/hil-sim/batches/' + value['id'], headers={'Authorization': 'Bearer ' + engineer}).status_code == 404
    tools = env[1].get('/api/v1/agent/tools', headers=headers).json()
    assert tools  # Routes remain under the normal versioned, role-gated API.


def test_sequential_worker_without_browser_and_sanitized_export(env):
    value, _ = submit(env, batch(env))
    for expected in range(1, 4):
        result = work(env)
        assert result.status == 'completed'
        assert count(env) == expected  # Never fan out the remaining conditions.
    advance(env[0], env[3])
    complete = get(env, value)
    assert complete['status'] == 'completed' and complete['summary'] == {'total': 3, 'completed': 3, 'attempts': 3}
    assert all(len(item['attempts']) == 1 for item in complete['items'])
    measured = complete['items'][0]['attempts'][0]['result']['sides']['faulted']
    assert measured['first_degraded_s'] == .05 and measured['first_recovered_s'] is None
    assert measured['peak_input_age_ms'] == 300 and measured['peak_brake'] == 1
    for item in complete['items']:
        history = env[1].get('/api/v1/hil-sim/tests/' + item['test_ref']['id'] + '/executions').json()
        assert history['items'][0]['execution_id'] == item['attempts'][0]['execution_id']
    exported = env[1].get('/api/v1/hil-sim/batches/' + value['id'] + '/export')
    assert exported.status_code == 200 and 'attachment;' in exported.headers['content-disposition']
    assert '/private/operator' not in exported.text and 'private_transport' not in exported.text
    assert len(exported.json()['tests']) == 3 and exported.json()['batch']['status'] == 'completed'


def test_lost_start_response_after_advancement_and_definition_drift(env):
    value = batch(env)
    first, payload = submit(env, value)
    advance(env[0], env[3])
    retry = env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/start', json=payload,
                        headers={'Idempotency-Key': 'start'})
    assert retry.status_code == 202 and retry.json() == first
    assert count(env) == 1
    assert env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/start', json={**payload, 'preview_hash': '0' * 64},
                      headers={'Idempotency-Key': 'start'}).status_code == 409
    assert env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/cancel',
                      json={'expected_revision': value['revision']}, headers={'Idempotency-Key': 'stale'}).status_code == 409


def test_preview_drift_is_rejected_without_child_and_registry_rechecked(env):
    value = batch(env)
    reviewed = preview(env, value).json()
    env[2].source_revision += 1
    response = env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/start',
        json={'expected_revision': value['revision'], 'preview_hash': reviewed['preview_hash']}, headers={'Idempotency-Key': 'drift'})
    assert response.status_code == 409 and count(env) == 0
    running, _ = submit(env, value)
    env[3]._handlers.pop(ADAPTER)
    advance(env[0], env[3])
    assert get(env, running)['status'] == 'stopped' and count(env) == 0 and count(env, ExperimentRow) == 0


def test_failure_stops_and_explicit_resume_keeps_prior_attempt(env):
    value, _ = submit(env, batch(env))
    advance(env[0], env[3])
    claim = jobs.claim_job(env[0], 'failure-fixture', capabilities={('experiment', ADAPTER, env[2].version)})
    jobs.fail_job(env[0], claim, 'Authored certain failure before engine dispatch', before_dispatch=True)
    advance(env[0], env[3])
    stopped = get(env, value)
    assert stopped['status'] == 'stopped' and stopped['can_resume']
    for _ in range(3):
        advance(env[0], env[3])
    assert count(env) == 1
    resumed, payload = submit(env, stopped, 'resume')
    work(env)
    current = get(env, value)
    assert count(env) == 2 and len(current['items'][0]['attempts']) == 2
    assert current['items'][0]['attempts'][0]['execution_id'] == claim.job.id
    assert current['items'][0]['attempts'][0]['status'] == 'failed'
    retry = env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/resume', json=payload, headers={'Idempotency-Key': 'resume'})
    assert retry.status_code == 202 and retry.json() == resumed


def test_next_dispatch_failure_preserves_completed_receipt_on_resume(env):
    value, _ = submit(env, batch(env))
    first = work(env)
    assert first.status == 'completed'
    handler = env[3]._handlers.pop(ADAPTER)
    advance(env[0], env[3])
    stopped = get(env, value)
    assert stopped['status'] == 'stopped' and count(env) == 1
    assert stopped['items'][0]['status'] == 'completed'
    assert stopped['items'][0]['attempts'][0]['result']['execution_id'] == first.id
    env[3]._handlers[ADAPTER] = handler
    assert [item['index'] for item in preview(env, stopped).json()['items']] == [1, 2]
    submit(env, stopped, 'resume')
    work(env)
    work(env)
    advance(env[0], env[3])
    complete = get(env, value)
    assert complete['status'] == 'completed' and count(env) == 3
    assert len(complete['items'][0]['attempts']) == 1
    assert complete['items'][0]['attempts'][0]['execution_id'] == first.id


def test_cancel_between_receipt_commit_and_enqueue_fences_next_child(env, monkeypatch):
    value, _ = submit(env, batch(env))
    first = work(env)
    original_write, armed = jobs._write, True

    @contextmanager
    def interleaved(db):
        nonlocal armed
        with original_write(db) as session:
            yield session
        if armed:
            armed = False  # Permit the cancellation's own transaction normally.
            committed = get(env, value)
            assert committed['items'][0]['status'] == 'completed'
            cancelled = cancel(env, committed)
            assert cancelled['status'] == 'cancelled'

    monkeypatch.setattr(jobs, '_write', interleaved)
    advance(env[0], env[3])
    stopped = get(env, value)
    assert stopped['status'] == 'cancelled' and count(env) == 1
    assert stopped['items'][0]['attempts'][0]['execution_id'] == first.id
    assert all(not item['attempts'] for item in stopped['items'][1:])


def test_queued_cancel_and_resume_never_reexecutes_completed_item(env):
    value, _ = submit(env, batch(env))
    work(env)  # First completed; the next one has not yet been accepted.
    advance(env[0], env[3])
    current = get(env, value)
    assert current['items'][0]['status'] == 'completed' and current['items'][1]['status'] == 'queued'
    first_id = current['items'][0]['attempts'][0]['execution_id']
    stopped = cancel(env, current)
    assert stopped['status'] == 'cancelled' and stopped['items'][2]['status'] == 'cancelled'
    probes = env[2].probes
    review = preview(env, stopped)
    assert review.status_code == 200 and [item['index'] for item in review.json()['items']] == [1, 2]
    assert env[2].probes == probes + 2
    submit(env, stopped, 'resume')
    work(env)
    work(env)
    advance(env[0], env[3])
    finished = get(env, value)
    assert finished['status'] == 'completed' and count(env) == 4
    assert finished['items'][0]['attempts'][0]['execution_id'] == first_id and len(finished['items'][0]['attempts']) == 1
    assert [attempt['status'] for attempt in finished['items'][1]['attempts']] == ['cancelled', 'completed']


def test_running_cancel_receipt_completion_race_stops_remaining(env):
    value, _ = submit(env, batch(env))
    work(env)  # Receipt is complete but batch state still records running.
    current = get(env, value)
    stopped = cancel(env, current)
    assert stopped['status'] == 'cancelled'
    assert stopped['items'][0]['status'] == 'completed'
    assert [item['status'] for item in stopped['items'][1:]] == ['cancelled', 'cancelled']
    advance(env[0], env[3])
    assert count(env) == 1


def test_running_cancel_acknowledgement_and_lost_cancel_response(env, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = env[2].execute
    def execution(snapshot, work_dir, should_cancel):
        entered.set()
        assert release.wait(5)
        # The normal worker intentionally caches cancellation for 50ms; model
        # the adapter's repeated polling, not a single immediate cached read.
        for _ in range(100):
            if should_cancel():
                raise AdapterCancelled('Authored cancellation acknowledgement; no native engine')
            threading.Event().wait(.01)
        return original(snapshot, work_dir, should_cancel)
    monkeypatch.setattr(env[2], 'execute', execution)
    value, _ = submit(env, batch(env))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work, env)
        try:
            assert entered.wait(5)
            current = get(env, value)
            identity = current['items'][0]['attempts'][0]['execution_id']
            assert current['items'][0]['status'] == 'queued'
            assert current['live_executions'][identity]['status'] == 'running'
            stopped = cancel(env, current)
            assert stopped['status'] == 'stopping'
            release.set()
            assert future.result(timeout=5).status == 'cancelled'
        finally:
            release.set()
    advance(env[0], env[3])
    final = get(env, value)
    assert final['status'] == 'cancelled' and final['can_resume'] and count(env) == 1
    response = env[1].post('/api/v1/hil-sim/batches/' + value['id'] + '/cancel',
        json={'project_id': 'default', 'expected_revision': current['revision']}, headers={'Idempotency-Key': 'cancel'})
    assert response.status_code == 200 and response.json() == stopped


def test_resume_requires_idle_resource_and_exact_original_snapshot(env, monkeypatch):
    original = env[2].metadata
    monkeypatch.setattr(env[2], 'metadata', lambda: original().model_copy(update={'resource_id': 'hil-fixture-resource'}))
    value, _ = submit(env, batch(env))
    advance(env[0], env[3])
    claim = jobs.claim_job(env[0], 'resource-fixture', capabilities={('experiment', ADAPTER, env[2].version)})
    jobs.fail_job(env[0], claim, 'Certain pre-dispatch failure', before_dispatch=True)
    advance(env[0], env[3])
    stopped = get(env, value)
    with jobs._write(env[0]) as session:
        session.get(EngineResourceRow, 'hil-fixture-resource').state = 'reconcile_required'
    assert not get(env, value)['can_resume'] and preview(env, stopped).status_code == 409
    with jobs._write(env[0]) as session:
        session.get(EngineResourceRow, 'hil-fixture-resource').state = 'idle'
    env[2].source_revision += 1
    assert preview(env, stopped).status_code == 409
    assert count(env) == 1


@pytest.mark.parametrize('fixture_worker', [False, True])
def test_coordinator_runs_only_before_claim_heartbeat(env, monkeypatch, fixture_worker):
    from ade2e import hil_batches
    value, _ = submit(env, batch(env))
    original, calls = hil_batches.advance, []
    def once(db, registry):
        calls.append(True)
        if len(calls) > 1:
            raise RuntimeError('A post-claim coordinator would orphan the lease')
        return original(db, registry)
    monkeypatch.setattr(hil_batches, 'advance', once)
    if fixture_worker:
        import importlib.util
        from pathlib import Path
        from types import SimpleNamespace
        path = Path(__file__).resolve().parents[2] / 'e2e/fixtures/hil_sim_beta_server.py'
        spec = importlib.util.spec_from_file_location('hil_batch_worker_fixture', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        app = SimpleNamespace(state=SimpleNamespace(db=env[0], experiment_registry=env[3], registry=Registry(),
            runtime_registry=None, sensor_registry=None, evaluator_registry=None, stack_registry=None))
        result = module.run_hil_once(app, 'batch-fixture-worker')
    else:
        result = work(env)
    assert result.status == 'completed' and len(calls) == 1 and count(env) == 1


def test_uncertain_outcome_stops_and_cannot_resume(env):
    value, _ = submit(env, batch(env))
    advance(env[0], env[3])
    claim = jobs.claim_job(env[0], 'expired-fixture', capabilities={('experiment', ADAPTER, env[2].version)})
    jobs.fail_job(env[0], claim, 'Authored lost worker', status='interrupted', outcome_uncertain=True)
    advance(env[0], env[3])
    stopped = get(env, value)
    assert stopped['status'] == 'stopped' and not stopped['can_resume']
    assert 'uncertain' in stopped['action_reason']
    assert preview(env, stopped).status_code == 409
    advance(env[0], env[3])
    assert count(env) == 1


def test_parallel_ticks_and_restart_reuse_exact_child(env):
    value, _ = submit(env, batch(env))
    barrier = threading.Barrier(2)
    def tick():
        barrier.wait()
        advance(env[0], env[3])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: tick(), range(2)))
    current = get(env, value)
    identity = current['items'][0]['attempts'][0]['execution_id']
    assert count(env) == 1
    env[0].engine.dispose()  # Re-open the same durable SQLite state, no process-local dispatcher state.
    advance(env[0], env[3])
    assert get(env, value)['items'][0]['attempts'][0]['execution_id'] == identity and count(env) == 1


def test_enqueue_membership_failure_rolls_back_whole_job(env, monkeypatch):
    from ade2e import hil_batches
    value, _ = submit(env, batch(env))
    original = hil_batches.beta._put
    def fail_link(session, project, kind, document):
        if kind == hil_batches.beta.LINK:
            raise RuntimeError('Authored failure between queue insert and membership')
        return original(session, project, kind, document)
    monkeypatch.setattr(hil_batches.beta, '_put', fail_link)
    advance(env[0], env[3])
    stopped = get(env, value)
    assert stopped['status'] == 'stopped' and count(env) == 0 and count(env, ExperimentRow) == 0
    monkeypatch.setattr(hil_batches.beta, '_put', original)
    submit(env, stopped, 'resume')
    advance(env[0], env[3])
    assert count(env) == 1


def test_cancel_fences_parallel_dispatch_and_bad_trace_stops_sequence(env, monkeypatch):
    value, _ = submit(env, batch(env))
    stopped = cancel(env, value)
    advance(env[0], env[3])
    assert count(env) == 0
    submit(env, stopped, 'resume')
    work(env)
    with env[0].read_session() as session:
        artifact = session.scalar(select(ExperimentArtifactRow).where(ExperimentArtifactRow.name == 'faulted-hil-trace.json'))
        path = experiment_jobs.artifact_path(env[0], artifact)
    path.write_text('tampered response trace')
    advance(env[0], env[3])
    failed = get(env, value)
    assert failed['status'] == 'stopped' and failed['resume_blocked'] and not failed['can_resume']
    assert count(env) == 1
