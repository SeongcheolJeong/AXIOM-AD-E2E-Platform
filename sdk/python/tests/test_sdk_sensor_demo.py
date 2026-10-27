"""Sensor Sim shared API transport checks; no native simulation is executed."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient


@pytest.mark.parametrize('preset', ['camera_fov', 'driving_camera_fov'])
def test_sensor_demo_transport_and_project_pins(preset):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.sensor_sim_presets(project_id='p')
        client.bind_sensor_sim_preset(preset, project_id='p', expected_revision=1,
            capture_spec={'schema_version': 'sensor-capture/1'}, saved_execution_id='job_a')
        client.preview_sensor_sim_preset(preset, project_id='p', expected_binding_revision=2,
            parameters={'front_fov_deg': 70})
    assert [(request.method, request.url.path) for request in requests] == [
        ('GET', '/api/v1/sensor-sim/presets'), ('POST', f'/api/v1/sensor-sim/presets/{preset}/bindings'),
        ('POST', f'/api/v1/sensor-sim/presets/{preset}/preview')]
    assert dict(requests[0].url.params) == {'project_id': 'p'}
    assert json.loads(requests[1].content)['saved_execution_id'] == 'job_a'
    assert json.loads(requests[2].content) == dict(project_id='p', expected_binding_revision=2,
        parameters={'front_fov_deg': 70})


@pytest.mark.parametrize('identity,params', [
    ('missing', {}), ('camera_fov', {'front_fov_deg': True}), ('camera_fov', {'front_fov_deg': 70.5}),
    ('camera_fov', {'front_fov_deg': float('nan')}), ('camera_fov', {'front_fov_deg': 90}),
    ('camera_fov', {'range': 30}), ('radar_range', {'radar_range_m': 101}),
    ('lidar_density', {'lidar_points_per_second': 8500}),
])
def test_invalid_parameters_fail_before_network(identity, params):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid request reached network'))) as client:
        with pytest.raises(ValueError):
            client.preview_sensor_sim_preset(identity, expected_binding_revision=1, parameters=params)


@pytest.mark.parametrize('kwargs', [{'expected_revision': True}, {'seed': True}, {'seed': -1},
    {'project_id': '../p'}, {'capture_spec': []}, {'saved_execution_id': '../job'}])
def test_invalid_binding_arguments_fail_before_network(kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid request reached network'))) as client:
        with pytest.raises(ValueError):
            client.bind_sensor_sim_preset('camera_fov', **kwargs)


@pytest.mark.parametrize('changed', [False, True])
def test_explicit_execution_uses_normal_queue_and_stops_on_preview_drift(changed):
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/executions'):
            return httpx.Response(202, json={'id': 'job_one', 'status': 'queued'})
        return httpx.Response(201, json={'id': 'experiment_one', 'preview_hash': ('b' if changed else 'a') * 64})
    preview = {'schema_version': 'sensor-sim-preset-preview/1',
        'experiment': {'name': 'Reviewed preset', 'kind': 'sensor_capture'}, 'preview': {'preview_hash': 'a' * 64}}
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        if changed:
            with pytest.raises(ValueError, match='no execution was submitted'):
                client.execute_sensor_sim_preview(preview, idempotency_key='explicit-run')
        else:
            assert client.execute_sensor_sim_preview(preview, idempotency_key='explicit-run')['status'] == 'queued'
    assert len(requests) == (1 if changed else 2)
    assert all(request.headers['Idempotency-Key'] == 'explicit-run' for request in requests)
    assert json.loads(requests[0].content) == preview['experiment']
