"""SDK wire and validation checks; no engine/network execution."""
import copy
import json
import httpx
import pytest
from axiom_sdk import AxiomClient

REF = dict(id='layout_one', revision=2, sha256='a' * 64)
RIG = dict(schema_version='carla-driving-multisensor-spec/1', duration_seconds=20, sample_hz=10, spawn_index=0,
    vehicle_blueprint='vehicle.tesla.model3', expected_map='Town10HD_Opt', expected_server_version='1ae5356', rig_revision=1,
    coordinate_convention='right_handed_x_forward_y_left_z_up',
    controller=dict(id='native-map-lane-follow/1', target_speed_mps=3, lookahead_m=6, route_length_m=150),
    sensors=[dict(key=key, kind=kind, translation_m=[1.5, 0, 1.8], rotation_deg=[0, 180 if key == 'rear' else 0, 0],
        rotation_order='pitch_yaw_roll_right_hand_rule', parameters={}) for key, kind in [('front', 'rgb'), ('rear', 'rgb'), ('lidar', 'lidar'), ('radar', 'radar')]])


def test_layout_crud_preview_atomic_execution_and_observation_share_api():
    requests = []
    def respond(request):
        requests.append(request); return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.sensor_sim_layout_templates(project_id='project_one')
        client.sensor_sim_layouts(project_id='project_one', limit=20, offset=1)
        client.save_sensor_sim_layout(name='Layout', rig_b=RIG, idempotency_key='save', layout_id='layout_one', expected_revision=1, copied_from=REF)
        client.sensor_sim_layout('layout_one', revision=2)
        client.sensor_sim_layout_revisions('layout_one')
        client.preview_sensor_sim_layout(REF)
        client.execute_sensor_sim_layout(REF, preview_hash='b' * 64, idempotency_key='run')
        client.sensor_sim_layout_executions('layout_one', revision=2)
        client.create_sensor_sim_layout_observation(layout_ref=REF, execution_id='job_one', sensor_key='lidar', side='rig_b',
            time_s=19.9, title='Observation', note='User interpretation', idempotency_key='note')
        client.sensor_sim_layout_observations(layout_id='layout_one', revision=2)
        client.export_sensor_sim_layout('layout_one', revision=2)
    assert len(requests) == 11
    assert requests[0].url.params['project_id'] == 'project_one'
    assert json.loads(requests[2].content)['copied_from'] == REF
    assert requests[3].url.params['revision'] == '2'
    assert json.loads(requests[5].content)['layout_ref'] == REF
    assert requests[6].headers['Idempotency-Key'] == 'run'
    assert json.loads(requests[6].content)['preview_hash'] == 'b' * 64
    assert json.loads(requests[8].content)['time_s'] == 19.9
    assert requests[10].url.path.endswith('/layout_one/export')


def test_clone_pins_historical_revision_and_hash():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={**REF, 'note': 'Original', 'rig_b': RIG} if request.method == 'GET' else {'saved': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        result = client.clone_sensor_sim_layout(REF, name='Cloned layout', idempotency_key='clone')
    assert result == {'saved': True} and requests[0].url.params['revision'] == '2'
    assert json.loads(requests[1].content)['copied_from'] == REF
    assert 'id' not in json.loads(requests[1].content)


@pytest.mark.parametrize('change', ['sensor_count', 'sensor_parameter', 'duration', 'translation_step', 'rotation_bound'])
def test_invalid_placement_does_not_reach_network(change):
    rig = copy.deepcopy(RIG)
    if change == 'sensor_count': rig['sensors'].pop()
    elif change == 'sensor_parameter': rig['sensors'][0]['parameters'] = {'fov': '70'}
    elif change == 'duration': rig['duration_seconds'] = 30
    elif change == 'translation_step': rig['sensors'][0]['translation_m'][0] = 1.51
    elif change == 'rotation_bound': rig['sensors'][0]['rotation_deg'][0] = 181
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Reached network'))) as client:
        with pytest.raises(ValueError):
            client.save_sensor_sim_layout(name='Invalid', rig_b=rig, idempotency_key='save')


@pytest.mark.parametrize('time', [20, -1, True, float('nan')])
def test_invalid_observation_clock_does_not_reach_network(time):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Reached network'))) as client:
        with pytest.raises(ValueError):
            client.create_sensor_sim_layout_observation(layout_ref=REF, execution_id='job_one', sensor_key='front',
                time_s=time, title='Observation', note='Note', idempotency_key='note')


def test_bad_ref_and_unscoped_revision_filter_fail_before_network():
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Reached network'))) as client:
        with pytest.raises(ValueError): client.preview_sensor_sim_layout({**REF, 'sha256': 'incorrect'})
        with pytest.raises(ValueError): client.sensor_sim_layout_observations(revision=2)
