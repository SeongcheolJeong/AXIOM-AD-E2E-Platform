"""Sealed authored report fixtures; these tests do not execute CARLA."""
import json
import pytest
from ade2e.job_models import JobRow
from ade2e import jobs
from test_object_demo import env, save, execute
from test_object_story import story_env, event


def action_event(at, kind, action_id, action_kind='wait', actor='rear'):
    return {**event(at, 'wait', actor), 'type': kind, 'reason': action_kind, 'action_id': action_id}


def response_frames(handler, scenario, *, stopped_at_goal=True):
    count = round(scenario['duration_s'] / scenario.get('timestep_s', .05))
    for index in range(count):
        handler.story_frame_actors[index] = [{'id': 'ego', 'x': 30 + 60 * index / (count - 1),
            'y': 0, 'z': 0, 'speed_mps': 0 if stopped_at_goal and index == count - 1 else 2}]


def read(client, identity, **params):
    return client.get(f'/api/v1/object-sim/executions/{identity}/behavior', params=params)


def test_report_separates_recorded_action_completion_from_measured_ego_outcome(story_env):
    _, client, handler, _, scenario = story_env
    scenario['actors'][2]['behavior'] = {'actions': [
        {'id': 'pause', 'kind': 'wait', 'duration_s': 2},
        {'id': 'later', 'kind': 'wait', 'trigger': {'kind': 'time', 'value': 50}, 'duration_s': 1},
        {'id': 'never', 'kind': 'wait', 'trigger': {'kind': 'time', 'value': 59}, 'duration_s': 1}]}
    handler.story_events = [action_event(2, 'action_started', 'pause'), action_event(4, 'action_completed', 'pause'),
        action_event(50, 'action_started', 'later'), event(58, 'arrived')]
    response_frames(handler, scenario, stopped_at_goal=False)
    ref = save(client, scenario); identity = execute(story_env, ref)
    result = read(client, identity)
    assert result.status_code == 200, result.text
    report = result.json()
    assert report['status'] == 'available' and report['scenario_ref'] == ref
    assert report['source']['artifact_sha256'] and report['physical_sensor_validation'] is False
    ego, rear = report['actors'][0], report['actors'][2]
    assert ego['mode'] == 'route_only' and ego['actions'] == []
    assert ego['planner_arrival_time_s'] == 58
    assert [action['status'] for action in rear['actions']] == ['completed', 'started', 'not_exercised']
    assert rear['actions'][0]['events'][0]['time_s'] == 2
    assert rear['actions'][0]['events'][0]['observation_time_s'] == pytest.approx(2.05)
    # An arrived planner state does not overwrite observed final speed.
    assert report['ego_outcome']['status'] == 'moving'
    assert report['ego_outcome']['goal_error_m'] == 0 and report['ego_outcome']['final_speed_mps'] == 2
    assert report['ego_outcome']['distance_travelled_m'] == pytest.approx(60)
    assert report['action_completion_meaning'] == 'planner_reported_completion_not_physical_validation'


@pytest.mark.parametrize('events,reason', [
    ([action_event(2, 'action_completed', 'pause')], 'sequence_invalid'),
    ([action_event(2, 'action_started', 'pause', 'cut_in')], 'kind_differs'),
    ([action_event(2, 'action_started', 'pause'), action_event(3, 'action_started', 'pause')], 'sequence_invalid')])
def test_invalid_action_clock_or_kind_is_unavailable_not_exercised(story_env, events, reason):
    _, client, handler, _, scenario = story_env
    scenario['actors'][2]['behavior'] = {'actions': [{'id': 'pause', 'kind': 'wait', 'duration_s': 1}]}
    handler.story_events = events
    ref = save(client, scenario); identity = execute(story_env, ref)
    value = read(client, identity).json()
    assert value['status'] == 'unavailable' and reason in value['reason']
    assert value['actors'][2]['actions'][0]['status'] == 'pending'
    assert value['source'] is None


@pytest.mark.parametrize('completion_reason', ['conflict_clear', 'yield_target_clear'])
def test_yield_completion_keeps_the_planners_observed_release_reason(story_env, completion_reason):
    _, client, handler, _, scenario = story_env
    scenario['actors'][2]['behavior'] = {'actions': [
        {'id': 'yield_to_ego', 'kind': 'yield', 'duration_s': 0, 'target_actor_id': 'ego'}]}
    handler.story_events = [action_event(2, 'action_started', 'yield_to_ego', 'yield'),
        action_event(4, 'action_completed', 'yield_to_ego', completion_reason)]
    response_frames(handler, scenario)
    ref = save(client, scenario); identity = execute(story_env, ref)
    report = read(client, identity).json()
    assert report['status'] == 'available'
    action = report['actors'][2]['actions'][0]
    assert action['kind'] == 'yield' and action['status'] == 'completed'
    assert action['started_at_s'] == 2 and action['completed_at_s'] == 4
    assert action['events'][-1]['reason'] == completion_reason
    assert report['action_completion_meaning'] == 'planner_reported_completion_not_physical_validation'


@pytest.mark.parametrize('kind,started_reason', [('wait', 'wait'), ('yield', 'conflict_clear')])
def test_yield_release_alias_does_not_mask_another_kind_or_invalid_start(story_env, kind, started_reason):
    _, client, handler, _, scenario = story_env
    scenario['actors'][2]['behavior'] = {'actions': [{'id': 'pause', 'kind': kind, 'duration_s': 0}]}
    handler.story_events = [action_event(2, 'action_started', 'pause', started_reason),
        action_event(4, 'action_completed', 'pause', 'conflict_clear')]
    ref = save(client, scenario); identity = execute(story_env, ref)
    report = read(client, identity).json()
    assert report['status'] == 'unavailable'
    assert report['reason'] == 'planner_action_kind_differs_from_authored_action'
    assert report['actors'][2]['actions'][0]['status'] == 'pending'


def test_pending_and_missing_source_do_not_invent_action_or_ego_results(story_env):
    db, client, handler, _, scenario = story_env
    scenario['actors'][2]['behavior'] = {'actions': [{'id': 'pause', 'kind': 'wait', 'duration_s': 1}]}
    response_frames(handler, scenario)
    ref = save(client, scenario); pending = execute(story_env, ref, complete=False)
    value = read(client, pending).json()
    assert value['status'] == 'pending' and value['ego_outcome']['status'] == 'unavailable'
    assert value['actors'][2]['actions'][0]['status'] == 'pending'
    identity = execute(story_env, ref, 'complete')
    value = read(client, identity).json()
    assert value['ego_outcome']['status'] == 'arrived'
    artifact = client.get('/api/v1/experiments/executions/' + identity).json()['artifacts'][0]
    (db.experiment_dir / 'artifacts' / artifact['id']).write_text('{}')
    value = read(client, identity).json()
    assert value['status'] == 'unavailable' and 'integrity' in value['reason']
    assert value['actors'][2]['actions'][0]['status'] == 'pending'


def test_behavior_report_is_project_authorized_and_readonly(story_env):
    _, client, _, _, scenario = story_env
    ref = save(client, scenario); identity = execute(story_env, ref)
    project = client.post('/api/v1/projects', json={'name': 'Other'}).json()['id']
    viewer = client.post('/api/v1/projects/default/tokens', json={'role': 'Viewer', 'user_label': 'reader'}).json()['token']
    other = client.post(f'/api/v1/projects/{project}/tokens', json={'role': 'Viewer', 'user_label': 'other'}).json()['token']
    client.headers['Authorization'] = 'Bearer ' + viewer
    assert read(client, identity).status_code == 200
    client.headers['Authorization'] = 'Bearer ' + other
    assert read(client, identity, project_id=project).status_code == 404


def test_interrupted_execution_can_receive_uncertain_issue_observation(env):
    _, client, _, _, scenario = env
    ref = save(client, scenario); identity = execute(env, ref, complete=False)
    db = env[0]
    # A stopped worker is a terminal failure even when evidence is incomplete.
    with db.session() as session:
        row = session.query(JobRow).filter_by(id=identity).one()
        jobs._terminal(session, row, 'interrupted', '2026-10-10T12:00:00+00:00', 'Authored interruption fixture', uncertain=True)
        session.commit()
    response = client.post('/api/v1/object-sim/observations', headers={'Idempotency-Key': 'interrupted'}, json={
        'execution_id': identity, 'scenario_ref': ref, 'time_s': 0, 'title': 'Execution interrupted',
        'note': 'No behavior conclusion because the worker stopped.', 'kind': 'issue'})
    assert response.status_code == 201, response.text
    assert response.json()['execution']['outcome_uncertain'] is True
    assert response.json()['behavior_exercised'] == 'uncertain'
