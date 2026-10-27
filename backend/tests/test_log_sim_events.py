"""Recorded-rule and access contracts; authored fixtures are not simulator proof."""
import copy
import math

import pytest
from fastapi import APIRouter, Depends, HTTPException, Request

from ade2e import log_sim, log_sim_events
from ade2e.database import Settings
from ade2e.log_sim_events import discover_events, register_log_sim_event_routes
from ade2e.team_access import authenticate_request
from test_log_sim import log_env, trace
from test_object_demo import env


SOURCE = {'id': 'test-source', 'kind': 'nuscenes', 'source_hash': 'a'*64}


def frames():
    return [dict(time_s=index*.5, timestamp_ns=str(100000000000+index*500000000),
        ego=dict(x=20., y=30., yaw_rad=math.pi/2, speed_mps=speed),
        objects=[dict(id='ahead', x=21., y=40.-index), dict(id='behind', x=20., y=20.),
                 dict(id='outside', x=25., y=35.)],
        sample_ref={'ego': {'ordinal': 2*index, 'sha256': str(index)*64},
                    'objects': {'ordinal': 2*index+1, 'sha256': str(index+1)*64}})
        for index, speed in enumerate((4., 3., .2, 0., 0., 2.))]


def test_rules_exact_intervals_rotated_distances_and_stable_pins():
    data = frames(); result = discover_events(SOURCE, data, {'recording_sha256': 'b'*64})
    assert result == discover_events(SOURCE, data, {'recording_sha256': 'b'*64})
    by_kind = {item['kind']: item for item in result['items']}
    assert set(by_kind) == {'low_speed', 'deceleration', 'nearby_ahead'}
    stopped = by_kind['low_speed']
    assert (stopped['time_s'], stopped['end_time_s']) == (1., 2.)
    assert stopped['evidence']['start_timestamp_ns'] == '101000000000'
    assert stopped['evidence']['sample_refs'][0]['ego']['ordinal'] == 4
    braking = by_kind['deceleration']
    assert (braking['time_s'], braking['end_time_s']) == (0., 1.)
    assert braking['evidence']['minimum_acceleration_mps2'] == pytest.approx(-5.6)
    nearby = by_kind['nearby_ahead']
    assert nearby['evidence']['object_id'] == 'ahead'
    assert nearby['evidence']['minimum_longitudinal_center_distance_m'] == pytest.approx(5.)
    assert nearby['evidence']['absolute_lateral_center_distance_m'] == pytest.approx(1.)
    assert all(item['severity'] == 'review' and item['verdict'] == 'not_evaluated' for item in result['items'])
    changed = discover_events(SOURCE | {'source_hash': 'c'*64}, data, {'recording_sha256': 'b'*64})
    assert changed['analysis_hash'] != result['analysis_hash']
    assert {x['id'] for x in changed['items']}.isdisjoint({x['id'] for x in result['items']})
    changed_data = copy.deepcopy(data); changed_data[0]['ego']['speed_mps'] = 5.
    assert discover_events(SOURCE, changed_data, {})['analysis_hash'] != discover_events(SOURCE, data, {})['analysis_hash']


def test_missing_time_not_bridged_and_absent_state_not_claimed_clear():
    data = frames()[:2]
    for frame in data:
        frame['ego']['speed_mps'] = 0.
        frame['objects'] = []
    data[1].update(time_s=2., timestamp_ns='102000000000')
    assert discover_events(SOURCE, data, {})['items'] == []
    data[0]['ego'].pop('speed_mps')
    with pytest.raises(HTTPException, match='complete recorded ego'):
        discover_events(SOURCE, data, {})


def test_speed_sample_jitter_does_not_produce_fragmented_braking():
    data = [dict(time_s=index*.05, timestamp_ns=str(index*50000000),
        ego=dict(x=0., y=0., yaw_rad=0., speed_mps=5.+(.1 if index % 2 else 0.)), objects=[])
        for index in range(40)]
    # Alternating 20Hz differences are +/-2 m/s², but the observed speed does not fall.
    assert discover_events(SOURCE, data, {})['items'] == []
    for index, frame in enumerate(data):
        frame['ego']['speed_mps'] = 5.-index*.1+(.05 if index % 2 else 0.)
    braking = [event for event in discover_events(SOURCE, data, {})['items'] if event['kind'] == 'deceleration']
    assert len(braking) == 1
    assert braking[0]['evidence']['method'] == 'windowed_recorded_speed_finite_difference'
    assert braking[0]['thresholds']['minimum_difference_window_s'] == .2


@pytest.mark.parametrize('mutation', [
    lambda data: data[1].update(time_s=data[0]['time_s']),
    lambda data: data[0]['ego'].update(x=float('nan')),
    lambda data: data[0].update(objects=None),
    lambda data: data[0].update(timestamp_ns='invalid'),
])
def test_unsupported_evidence_fails_closed(mutation):
    data = frames(); mutation(data)
    with pytest.raises(HTTPException) as caught:
        discover_events(SOURCE, data, {})
    assert caught.value.status_code == 409


@pytest.fixture
def event_env(log_env):
    environment, _, _ = log_env
    db, client, _, _, _ = environment
    def auth(request: Request):
        authenticate_request(request, db, Settings(api_token='test-bootstrap', team_mode=True, seed_examples=False),
            request.headers.get('authorization', '')[7:])
    router = APIRouter(prefix='/api/v1', dependencies=[Depends(auth)])
    register_log_sim_event_routes(router, db); client.app.include_router(router)
    return environment


def test_native_route_queries_stable_analysis_and_project_scope(event_env, monkeypatch):
    db, client, _, _, _ = event_env
    monkeypatch.setattr(log_sim_events, 'source_frames', lambda *args: (frames(), {'authored_fixture': True}))
    url = '/api/v1/log-sim/sources/carla-urban-right-turn/events'
    all_events = client.get(url).json()
    assert all_events['total'] == 3 and all_events['schema_version'] == 'log-sim-event-search/1'
    response = client.get(url, params={'q': '정지', 'limit': 1})
    assert response.status_code == 200, response.text
    filtered = response.json()
    assert filtered['total'] == 1 and filtered['items'][0]['kind'] == 'low_speed'
    assert filtered['analysis_hash'] == all_events['analysis_hash']
    assert client.get(url, params={'kind': 'nearby_ahead', 'q': 'ahead'}).json()['total'] == 1
    assert client.get(url, params={'start_time_s': 2.1}).json()['total'] == 1
    assert client.get(url, params={'offset': 3}).json()['items'] == []
    for query in ({'limit': 101}, {'kind': 'collision'}, {'q': 'x'*121},
                  {'start_time_s': 2, 'end_time_s': 1}, {'start_time_s': 'nan'}):
        assert client.get(url, params=query).status_code == 422
    other = client.post('/api/v1/projects', json={'name': 'Other'}).json()['id']
    token = client.post(f'/api/v1/projects/{other}/tokens', json={'user_label': 'Other', 'role': 'Viewer'}).json()['token']
    assert client.get(url, headers={'Authorization': 'Bearer '+token}).status_code == 404
    viewer = client.post('/api/v1/projects/default/tokens', json={'user_label': 'View', 'role': 'Viewer'}).json()['token']
    assert client.get(url, headers={'Authorization': 'Bearer '+viewer}).status_code == 200


def test_road_route_enforces_recording_grant_before_read(event_env, monkeypatch):
    _, client, _, _, _ = event_env
    monkeypatch.setattr(log_sim, '_source', lambda *args: SOURCE | {'recording_ref': {'id': 'not_granted'}})
    monkeypatch.setattr(log_sim_events, 'source_frames', lambda *args: pytest.fail('Unapproved recording read'))
    viewer = client.post('/api/v1/projects/default/tokens', json={'user_label': 'View', 'role': 'Viewer'}).json()['token']
    response = client.get('/api/v1/log-sim/sources/test-source/events', headers={'Authorization': 'Bearer '+viewer})
    assert response.status_code == 404


def test_native_source_normalizes_clock_and_preserves_execution_provenance(event_env, monkeypatch):
    db, _, _, _, _ = event_env
    monkeypatch.setattr(log_sim, '_scene', lambda *args: (trace(), [], {'artifact_sha256': 'f'*64}))
    with db.read_session() as session:
        source = log_sim._source(session, 'default', log_sim.NATIVE_ID)
        data, receipt = log_sim_events.source_frames(db, session, 'default', source)
    assert data[0]['timestamp_ns'] == '50000000'
    assert data[0]['ego']['y'] == pytest.approx(-21.)
    assert data[0]['sample_ref'] == {'scene_frame_index': 0}
    assert receipt['adapter_id'] == 'demo-contract' and receipt['seed'] == 42
    assert receipt['fidelity'] == 'authored_demo_contract'
