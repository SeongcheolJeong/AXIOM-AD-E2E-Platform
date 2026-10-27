"""Normal queue over authored retained MCAP; these are not learned/native-model runs."""
import pytest
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from ade2e import experiment_jobs, log_sim, log_sim_replays
from ade2e.adapters import Registry
from ade2e.database import Settings
from ade2e.experiment_models import ExperimentArtifactRow
from ade2e.job_models import JobRow
from ade2e.planning_replay import PlanningReplayHandler
from ade2e.recording_models import RecordingRow
from ade2e.scene_sequences import get_scene_sequence
from ade2e.team_access import authenticate_request
from ade2e.worker import run_once
from test_log_sim import log_env
from test_object_demo import env


def reference_trace():
    actors = [dict(id=identity, role=role, bbox_center={'x': 0., 'y': 0.},
                   dimensions_m={'length_m': 4., 'width_m': 2.})
              for identity, role in [('ego', 'ego'), ('lead', 'other')]]
    return dict(coordinate_system='carla_world_m_x_forward_y_right_z_up', actors=actors,
                frames=[dict(time_s=.05+i*.5, actors=[
                    dict(id='ego', x=i*1.6, y=0., heading_rad=0., speed_mps=3.2,
                         velocity={'x': 3.2, 'y': 0.}),
                    dict(id='lead', x=18., y=0., heading_rad=0., speed_mps=0.,
                         velocity={'x': 0., 'y': 0.})]) for i in range(6)])


@pytest.fixture
def workflow(log_env, monkeypatch):
    values, _, original_job = log_env
    db, client, _, registry, _ = values
    registry.register(PlanningReplayHandler(db))

    def auth(request: Request):
        authenticate_request(request, db, Settings(api_token='test-bootstrap', team_mode=True,
                             seed_examples=False), request.headers.get('authorization', '')[7:])

    api = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    log_sim_replays.register_log_sim_replay_routes(api, db, registry)
    client.app.include_router(api)
    monkeypatch.setattr(log_sim, '_scene', lambda *args: (
        reference_trace(), [], {'execution_id': original_job, 'artifact_sha256': 'a'*64}))
    response = client.post('/api/v1/log-sim/sources/carla-urban-right-turn/cases', json={
        'title': 'Two samples of the retained log', 'note': 'Authored geometry fixture only',
        'start_time_s': .5, 'end_time_s': 1.1}, headers={'Idempotency-Key': 'subset-case'})
    assert response.status_code == 201, response.text
    case = response.json()
    assert case['segment']['sample_count'] == 2
    body = dict(source_id='carla-urban-right-turn', case_id=case['id'], case_revision=case['revision'])
    return db, client, registry, body


def job_count(db):
    with db.read_session() as session:
        return session.scalar(select(func.count()).select_from(JobRow))


def preview(client, body):
    response = client.post('/api/v1/log-sim/comparisons/preview', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, body, key='pair'):
    p = preview(client, body)
    response = client.post('/api/v1/log-sim/comparisons', json=body | {'preview_hash': p['preview_hash']},
                           headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    return response.json(), p


def complete(db, client, registry, pair):
    for identity in pair['execution_ids'].values():
        assert run_once(db, Registry(), 'log-replay-fixture-worker', job_id=identity,
                        experiment_registry=registry)
    response = client.get('/api/v1/log-sim/comparisons/'+pair['id'])
    assert response.status_code == 200, response.text
    return response.json()


def test_pair_executes_all_source_samples_and_keeps_case_as_focus(workflow):
    db, client, registry, body = workflow
    before = job_count(db)
    p = preview(client, body)
    assert job_count(db) == before
    assert p['frame_count'] == 6 and p['case_frame_count'] == 2
    assert p['case_indices'] == [1, 2]
    assert p['mode'] == 'open_loop' and p['learned_model'] is False
    pair, _ = submit(client, body)
    assert pair['status'] == 'queued' and pair['result'] is None
    assert job_count(db) == before+2
    result = complete(db, client, registry, pair)
    assert result['status'] == 'completed' and result['result']['status'] == 'comparable'
    assert result['result']['frame_count'] == 6
    assert result['result']['changed_request_count'] > 0
    assert [s['index'] for s in result['result']['samples'] if s['in_case']] == [1, 2]
    with db.read_session() as session:
        sequence = get_scene_sequence(session, pair['sequence_id'])
        assert sequence.sha256 == pair['sequence_sha256'] == p['sequence_sha256']
        assert len(sequence.snapshot['frames']) == 6
        for side, identity in pair['execution_ids'].items():
            detail, ticks = log_sim_replays._trace(db, session, identity, 'default')
            assert detail['summary']['controller'] == pair['profiles'][side]['controller']
            assert detail['summary']['seed'] == 42
            assert detail['summary']['camera_feedback'] == 'not_supported_nonreactive_source_pixels'
            assert all(tick['ego'] == frame['ego'] and tick['logged_ego'] == frame['ego']
                       and tick['world_objects'] == frame['objects']['objects']
                       and tick['control_application'] == 'predicted_not_applied_open_loop'
                       for tick, frame in zip(ticks, sequence.snapshot['frames']))


def test_comparison_idempotence_and_stale_preview_create_no_extra_jobs(workflow):
    db, client, _, body = workflow
    count = job_count(db)
    p = preview(client, body)
    bad = client.post('/api/v1/log-sim/comparisons', json=body | {'preview_hash': '0'*64},
                      headers={'Idempotency-Key': 'bad'})
    assert bad.status_code == 409 and job_count(db) == count
    payload = body | {'preview_hash': p['preview_hash']}
    first = client.post('/api/v1/log-sim/comparisons', json=payload, headers={'Idempotency-Key': 'same'})
    assert first.status_code == 202, first.text
    second = client.post('/api/v1/log-sim/comparisons', json=payload, headers={'Idempotency-Key': 'same'})
    assert second.json() == first.json() and job_count(db) == count+2
    changed = client.post('/api/v1/log-sim/comparisons', json=payload | {'candidate_profile': 'responsive'},
                          headers={'Idempotency-Key': 'same'})
    assert changed.status_code == 409 and job_count(db) == count+2
    assert client.post('/api/v1/log-sim/comparisons/preview', json=body | {'case_id': 'foreign'}).status_code == 404
    assert client.post('/api/v1/log-sim/comparisons/preview', json=body | {'case_revision': 99}).status_code == 404
    assert client.post('/api/v1/log-sim/comparisons/preview', json=body | {'candidate_profile': 'learned-model'}).status_code == 422


def test_same_profile_has_no_fabricated_request_delta(workflow):
    db, client, registry, body = workflow
    pair, _ = submit(client, body | {'candidate_profile': 'balanced'})
    report = complete(db, client, registry, pair)['result']
    assert report['changed_request_count'] == report['case_changed_request_count'] == 0
    assert report['first_changed_time_s'] is None
    assert all(sample['baseline'] == sample['candidate'] for sample in report['samples'])
    assert all(metric['delta'] == 0 for metric in report['metrics'])


@pytest.mark.parametrize('limit,expected', [(10., 'pass'), (0., 'fail')])
def test_saved_regression_runs_real_pair_and_repeats_exact_outputs(workflow, limit, expected):
    db, client, registry, body = workflow
    pair, _ = submit(client, body)
    pair = complete(db, client, registry, pair)
    saved = client.post('/api/v1/log-sim/comparisons/'+pair['id']+'/regressions',
                        json={'name': 'Pinned request regression', 'max_target_speed_mps': limit},
                        headers={'Idempotency-Key': 'save'})
    assert saved.status_code == 201, saved.text
    recipe = saved.json()
    assert recipe['reviewer_approval'] is False
    assert recipe['evaluation_scope'] == 'diagnostic_open_loop_request_limits'
    assert recipe['saved_output_hash'] == pair['result']['output_hash']
    run = client.post('/api/v1/log-sim/regressions/'+recipe['id']+'/runs', json={},
                      headers={'Idempotency-Key': 'repeat'})
    assert run.status_code == 202, run.text
    assert run.json()['verdict'] == 'not_evaluated' and run.json()['matches_saved_outputs'] is None
    rerun = client.get('/api/v1/log-sim/comparisons/'+run.json()['comparison_id']).json()
    assert rerun['sequence_id'] == pair['sequence_id']
    assert rerun['replay_spec_ids'] == pair['replay_spec_ids']
    assert set(rerun['execution_ids'].values()).isdisjoint(pair['execution_ids'].values())
    result = complete(db, client, registry, rerun)
    assert result['result']['output_hash'] == pair['result']['output_hash']
    assert result['result']['samples'] == pair['result']['samples']
    report = client.get('/api/v1/log-sim/regressions').json()['items'][0]['runs'][0]
    assert report['status'] == 'completed' and report['verdict'] == expected
    assert report['matches_saved_outputs'] is True
    count = job_count(db)
    repeat = client.post('/api/v1/log-sim/regressions/'+recipe['id']+'/runs', json={},
                         headers={'Idempotency-Key': 'repeat'})
    assert repeat.json()['id'] == run.json()['id'] and job_count(db) == count
    listed = client.get('/api/v1/log-sim/comparisons').json()['items']
    assert {item['id'] for item in listed} == {pair['id'], rerun['id']}


def test_incomplete_pair_cannot_be_saved_or_called_comparable(workflow):
    db, client, registry, body = workflow
    pair, _ = submit(client, body)
    response = client.post('/api/v1/log-sim/comparisons/'+pair['id']+'/regressions',
                           json={'name': 'Premature'}, headers={'Idempotency-Key': 'premature'})
    assert response.status_code == 409
    assert run_once(db, Registry(), 'log-partial-worker', job_id=pair['execution_ids']['baseline'],
                    experiment_registry=registry)
    assert client.get('/api/v1/log-sim/comparisons/'+pair['id']).json()['result'] is None
    cancelled = client.post('/api/v1/experiments/executions/'+pair['execution_ids']['candidate']+'/cancel')
    assert cancelled.status_code == 200, cancelled.text
    report = client.get('/api/v1/log-sim/comparisons/'+pair['id']).json()
    assert report['status'] == 'cancelled' and report['result']['status'] == 'not_evaluated'


def test_project_and_role_boundaries_preserve_read_only_viewer(workflow):
    db, client, _, body = workflow
    other = client.post('/api/v1/projects', json={'name': 'Foreign'}).json()['id']
    def headers(role, project='default'):
        token = client.post('/api/v1/projects/'+project+'/tokens', json={'user_label': role, 'role': role}).json()['token']
        return {'Authorization': 'Bearer '+token, 'Idempotency-Key': role}
    viewer, engineer, foreign = headers('Viewer'), headers('Engineer'), headers('Engineer', other)
    p = client.post('/api/v1/log-sim/comparisons/preview', json=body, headers=viewer)
    assert p.status_code == 200, p.text
    payload = body | {'preview_hash': p.json()['preview_hash']}
    assert client.post('/api/v1/log-sim/comparisons', json=payload, headers=viewer).status_code == 403
    created = client.post('/api/v1/log-sim/comparisons', json=payload, headers=engineer)
    assert created.status_code == 202, created.text
    url = '/api/v1/log-sim/comparisons/'+created.json()['id']
    assert client.get(url, headers=viewer).status_code == 200
    assert client.get(url, headers=foreign).status_code == 404
    assert client.get(url+'?project_id='+other, headers=foreign).status_code == 404
    assert client.post(url+'/regressions', json={'name': 'No write'}, headers=viewer).status_code == 403
    assert client.get('/api/v1/cases/'+body['case_id'], headers=engineer).status_code == 403


def test_original_bytes_change_blocks_before_queue_submission(workflow):
    db, client, _, body = workflow
    p = preview(client, body)
    with db.read_session() as session:
        row = session.get(RecordingRow, p['recording_ref']['id'])
        path = db.recording_dir/row.storage_key
    raw = path.read_bytes()
    path.write_bytes(raw[:-1]+bytes([raw[-1] ^ 1]))
    before = job_count(db)
    response = client.post('/api/v1/log-sim/comparisons', json=body | {'preview_hash': p['preview_hash']},
                           headers={'Idempotency-Key': 'changed-original'})
    assert response.status_code == 422 and job_count(db) == before


@pytest.mark.parametrize('field', ['source_ego', 'logged_ego', 'world_objects', 'control_application', 'seed'])
def test_changed_source_observation_is_not_comparable(workflow, field):
    db, client, registry, body = workflow
    pair, _ = submit(client, body)
    pair = complete(db, client, registry, pair)
    with db.read_session() as session:
        sequence = get_scene_sequence(session, pair['sequence_id'])
        details, traces = {}, {}
        for side, identity in pair['execution_ids'].items():
            details[side], traces[side] = log_sim_replays._trace(db, session, identity, 'default')
        tick = traces['candidate'][0]
        if field == 'source_ego':
            tick[field]['message_sha256'] = '0'*64
        elif field == 'logged_ego':
            tick[field]['speed_mps'] += 1
        elif field == 'world_objects':
            tick[field][0]['x_m'] += 1
        elif field == 'control_application':
            tick[field] = 'applied_to_next_reference_state'
        else:
            details['candidate']['summary']['seed'] += 1
        result = log_sim_replays._result(pair, sequence, details, traces)
    assert result['status'] == 'not_comparable'


def test_interrupted_pair_remains_unevaluated(workflow):
    db, client, _, body = workflow
    pair, _ = submit(client, body)
    with db.session.begin() as session:
        row = session.scalar(select(JobRow).where(JobRow.id == pair['execution_ids']['candidate']))
        row.status = 'interrupted'
        row.error = 'Authored worker loss fixture'
        row.outcome_uncertain = True
        experiment_jobs.seal_terminal(session, row)
    report = client.get('/api/v1/log-sim/comparisons/'+pair['id']).json()
    assert report['status'] == 'failed'
    assert report['result']['status'] == 'not_evaluated'


def test_completed_uncertain_pair_cannot_become_regression_pass(workflow, monkeypatch):
    db, client, registry, body = workflow
    pair, _ = submit(client, body)
    complete(db, client, registry, pair)
    original_trace = log_sim_replays._trace
    def uncertain_trace(*args):
        detail, ticks = original_trace(*args)
        if detail['id'] == pair['execution_ids']['candidate']:
            detail['outcome_uncertain'] = True
        return detail, ticks
    monkeypatch.setattr(log_sim_replays, '_trace', uncertain_trace)
    report = client.get('/api/v1/log-sim/comparisons/'+pair['id']).json()
    assert report['status'] == 'completed' and report['result']['status'] == 'not_evaluated'
    response = client.post('/api/v1/log-sim/comparisons/'+pair['id']+'/regressions',
                           json={'name': 'Uncertain'}, headers={'Idempotency-Key': 'uncertain'})
    assert response.status_code == 409


def test_corrupt_retained_trace_cannot_be_reported_as_a_result(workflow):
    db, client, registry, body = workflow
    pair, _ = submit(client, body)
    complete(db, client, registry, pair)
    with db.read_session() as session:
        artifact = session.scalar(select(ExperimentArtifactRow).where(
            ExperimentArtifactRow.job_id == pair['execution_ids']['candidate'],
            ExperimentArtifactRow.name == 'planning-trace.ndjson'))
        path = db.experiment_dir/'artifacts'/artifact.storage_key
    path.write_bytes(path.read_bytes()+b'\n')
    response = client.get('/api/v1/log-sim/comparisons/'+pair['id'])
    assert response.status_code == 409
