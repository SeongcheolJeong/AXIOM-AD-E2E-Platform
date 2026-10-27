"""Common Sensor beta integration with authored queue/domain fixtures, no CARLA.

Observation rows here are authored domain fixtures. Native artifact validation is
owned by the sensor-layout tests; these tests check common task links and access.
"""
from urllib.parse import parse_qs
import pytest
from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete
from ade2e import sensor_layouts
from ade2e.adapters import Registry
from ade2e.beta_workspace import register_beta_workspace_routes
from ade2e.database import Settings
from ade2e.sensor_layout_schemas import ADAPTER, VERSION, baseline_rig
from ade2e.service import digest, utcnow
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.team_access import authenticate_request
from ade2e.worker import run_once
from test_sensor_demo import env as sensor_env


@pytest.fixture
def env(sensor_env, monkeypatch):
    db, client, _, registry = sensor_env
    handler = registry._handlers[ADAPTER]
    handler.version = VERSION
    execute = handler.execute
    def fixture_execute(snapshot, work_dir, should_cancel):
        result = execute(snapshot, work_dir, should_cancel)
        for side, rig in snapshot['rigs'].items():
            result.summary['captures'][side]['configuration_hash'] = digest(rig)
        return result
    monkeypatch.setattr(handler, 'execute', fixture_execute)
    def auth(request: Request):
        authenticate_request(request, db, Settings(api_token='test-bootstrap', team_mode=True, seed_examples=False),
                             request.headers.get('authorization', '')[7:])
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    sensor_layouts.register_sensor_layout_routes(router, db, registry)
    register_beta_workspace_routes(router, db, registry)
    client.app.include_router(router)
    return sensor_env


def post(env, path, body, key, status=200, token=None):
    headers = {'Idempotency-Key': key}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    response = env[1].post('/api/v1/' + path, json=body, headers=headers)
    assert response.status_code == status, response.text
    return response.json()


def task(env, key='task', mode='new_execution', **changes):
    return post(env, 'beta/tasks', dict(project_id='default', module='sensor', title='Camera direction',
        guide_id='camera_direction', mode=mode, **changes), key, 201)


def layout(env, key='layout', previous=None):
    rig = baseline_rig()
    rig['sensors'][0]['rotation_deg'][1] = -15 if previous is None else -20
    body = dict(project_id='default', name='Direction A/B', rig_b=rig)
    if previous:
        body.update(id=previous['id'], expected_revision=previous['revision'])
    return post(env, 'sensor-sim/layouts', body, key, 201)


def progress(env, value, key='progress', status=200, **updates):
    return post(env, 'beta/tasks/' + value['id'] + '/progress',
        dict(project_id=value['project_id'], expected_revision=value['revision'], **updates), key, status)


def execute(env, source, key='execute'):
    action = dict(project_id='default', layout_ref=sensor_layouts._ref(source))
    preview = post(env, 'sensor-sim/layouts/' + source['id'] + '/preview', action, key + '-preview')
    link = post(env, 'sensor-sim/layouts/' + source['id'] + '/executions',
        dict(**action, preview_hash=preview['preview']['preview_hash']), key, 202)
    run_once(env[0], Registry(), 'common-beta-authored-fixture', job_id=link['execution_id'], experiment_registry=env[3])
    return link['execution_id']


def observation(env, source, execution, identity='fixture-note', **updates):
    with env[0].session() as session:
        value = sensor_layouts._put(session, 'default', sensor_layouts.OBSERVATION,
            dict(schema_version='sensor-layout-observation/1', id=identity, revision=1,
                 project_id='default', layout_ref=sensor_layouts._ref(source), execution_id=execution,
                 sensor_key='front', side='rig_b', time_s=19.9, title='Authored note fixture',
                 note='Common task link check only', kind='observation', created_at=utcnow(), **updates))
        session.commit()
        return value


def test_sensor_guides_offline_authoring_exact_revisions_and_project_access(env):
    current = task(env)
    assert current['guide_id'] == 'camera_direction' and 'mode=layout' in current['resume_url']
    assert task(env) == current
    source = layout(env)
    linked = progress(env, current, source_ref=sensor_layouts._ref(source))
    assert linked['duration_s'] == 19.9 and linked['stage'] == 'execution'
    query = parse_qs(linked['resume_url'].split('?')[1])
    assert query['layout'] == [source['id']] and query['revision'] == ['1'] and query['guide'] == ['camera_direction']
    help_body = dict(project_id='default', module='sensor', task_id=linked['id'], kind='help', message='Need execution setup')
    post(env, 'beta/feedback', {**help_body, 'time_s': 1}, 'source-only-time', 422)
    source_help = post(env, 'beta/feedback', help_body, 'source-only-help', 201)
    assert source_help['context']['source_ref'] == sensor_layouts._ref(source)
    assert 'time_s=' not in source_help['context']['resume_url']
    wrong = task(env, 'wrong')
    progress(env, wrong, 'hash', status=409, source_ref={**sensor_layouts._ref(source), 'sha256': '0' * 64})
    progress(env, wrong, 'revision', status=404, source_ref={**sensor_layouts._ref(source), 'revision': 9})
    readiness = env[1].get('/api/v1/beta/readiness').json()
    sensor = next(item for item in readiness['items'] if item['module'] == 'sensor')
    assert sensor['guided'] and sensor['new_execution']['status'] == 'unknown'
    assert not sensor['new_execution']['worker_verified'] and 'mode=layout' in sensor['entry_url']
    assert env[3]._handlers[ADAPTER].probes == 0
    token = env[1].post('/api/v1/projects/default/tokens', json=dict(user_label='Reader', role='Viewer')).json()['token']
    post(env, 'beta/tasks', dict(project_id='default', module='sensor', title='No write', mode='new_execution'), 'viewer', 403, token)
    other = env[1].post('/api/v1/projects', json=dict(name='Other')).json()['id']
    foreign = post(env, 'beta/tasks', dict(project_id=other, module='sensor', title='Other', mode='retained_playback'), 'foreign', 201)
    progress(env, foreign, 'cross-project', status=404, source_ref=sensor_layouts._ref(source))
    post(env, 'beta/tasks', dict(project_id='default', module='object', title='No Sensor guide', mode='new_execution', guide_id='lidar_height'), 'wrong-guide', 422)


def test_sensor_completed_task_restores_exact_layout_execution_observation_and_feedback(env):
    current, source = task(env), layout(env)
    execution = execute(env, source)
    saved = observation(env, source, execution)
    current = progress(env, current, source_ref=sensor_layouts._ref(source), execution_id=execution,
        observation_id=saved['id'], result_opened=True, time_s=19.9, sensor_key='front')
    assert current['status'] == 'completed' and current['completion_basis'] == 'new_execution_observation'
    assert current['duration_s'] == 19.9
    query = parse_qs(current['resume_url'].split('?')[1])
    assert {key: query[key][0] for key in ('mode', 'layout', 'revision', 'result', 'time_s', 'sensor', 'observation', 'beta_task')} == {
        'mode': 'layout', 'layout': source['id'], 'revision': '1', 'result': execution,
        'time_s': '19.9', 'sensor': 'front', 'observation': saved['id'], 'beta_task': current['id']}
    assert env[1].get('/api/v1/beta/tasks/' + current['id']).json() == current
    evidence = env[1].get('/api/v1/beta/evidence?module=sensor').json()['items']
    assert any(item['kind'] == 'execution' and item['execution_id'] == execution and item['duration_s'] == 19.9 for item in evidence)
    assert any(item['kind'] == 'observation' and item['sensor_key'] == 'front' and item['time_s'] == 19.9 for item in evidence)
    report = post(env, 'beta/feedback', dict(project_id='default', module='sensor', task_id=current['id'], kind='completion',
        message='Authored beta fixture completed', time_s=19.9), 'feedback', 201)
    assert report['context']['sensor_key'] == 'front' and report['context']['source_ref'] == sensor_layouts._ref(source)
    assert 'observation=' + saved['id'] in report['context']['resume_url']
    post(env, 'beta/feedback', dict(project_id='default', module='sensor', task_id=current['id'], kind='issue',
        message='Observation bookmark must keep its timestamp', time_s=19.8), 'wrong-feedback-time', 409)
    triaged = post(env, 'beta/feedback/' + report['id'] + '/triage', dict(project_id='default', expected_revision=1,
        status='resolved', note='Fixture reviewed'), 'triage')
    assert triaged['status'] == 'resolved'
    assert current['id'] in env[1].get('/api/v1/beta/overview?format=csv').text


def test_sensor_retained_runs_and_layout_links_cannot_complete_a_different_new_task(env):
    source = layout(env)
    execution = execute(env, source)
    new = task(env)
    progress(env, new, 'retained-as-new', status=409, source_ref=sensor_layouts._ref(source), execution_id=execution)
    retained = task(env, 'retained', mode='retained_playback')
    retained = progress(env, retained, 'retained-link', source_ref=sensor_layouts._ref(source), execution_id=execution)
    assert retained['stage'] == 'investigation' and retained['duration_s'] == 19.9
    progress(env, retained, 'over-time', status=422, time_s=20)
    revised = layout(env, 'revised', source)
    wrong = task(env, 'wrong-revision', mode='retained_playback')
    progress(env, wrong, 'wrong-layout-link', status=409, source_ref=sensor_layouts._ref(revised), execution_id=execution)
    choices = env[1].get('/api/v1/beta/evidence?module=sensor').json()['items']
    assert {row['source_ref']['revision'] for row in choices if row['kind'] == 'saved_revision'} == {1, 2}
    with env[0].session() as session:
        session.execute(delete(SimulationCatalogRevisionRow).where(SimulationCatalogRevisionRow.kind == sensor_layouts.LINK))
        session.commit()
    unlinked = task(env, 'unlinked', mode='retained_playback')
    progress(env, unlinked, 'missing-link', status=409, source_ref=sensor_layouts._ref(source), execution_id=execution)


def test_sensor_observation_must_match_execution_sensor_and_timestamp(env):
    current, source = task(env), layout(env)
    first = execute(env, source)
    saved = observation(env, source, first)
    second = execute(env, source, 'second')
    progress(env, current, 'wrong-execution', status=409, source_ref=sensor_layouts._ref(source), execution_id=second, observation_id=saved['id'])
    progress(env, current, 'wrong-sensor', status=409, source_ref=sensor_layouts._ref(source), execution_id=first, observation_id=saved['id'], sensor_key='lidar')
    progress(env, current, 'wrong-time', status=409, source_ref=sensor_layouts._ref(source), execution_id=first, observation_id=saved['id'], time_s=19.8)
    linked = progress(env, current, 'derived-observation-context', source_ref=sensor_layouts._ref(source), execution_id=first,
        observation_id=saved['id'], result_opened=True)
    assert linked['sensor_key'] == 'front' and linked['time_s'] == 19.9 and linked['status'] == 'completed'
