"""Shared Neural Sim API transport; no GPU or rendering claims."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_sources_bindings_preview_and_reconstruction_use_shared_api():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.neural_sim_sources(project_id='project_a')
        client.neural_sim_presets(project_id='project_a')
        client.bind_neural_sim_preset('left', source_execution_id='job_source', reconstruction_execution_id='job_scene',
            saved_execution_id='job_render', expected_revision=2, project_id='project_a')
        client.preview_neural_sim_preset('left', expected_binding_revision=3, parameters={'lateral_m': -.2}, mode='frame', frame_index=8)
        client.preview_neural_reconstruction('job_source')
    assert [(item.method, item.url.path) for item in requests] == [
        ('GET', '/api/v1/neural-sim/sources'), ('GET', '/api/v1/neural-sim/presets'),
        ('POST', '/api/v1/neural-sim/presets/left/bindings'), ('POST', '/api/v1/neural-sim/presets/left/preview'),
        ('POST', '/api/v1/experiments/preview')]
    assert json.loads(requests[2].content)['reconstruction_execution_id'] == 'job_scene'
    assert json.loads(requests[3].content)['mode'] == 'frame'
    reconstruction = json.loads(requests[4].content)
    assert reconstruction['kind'] == 'neural_reconstruction'
    assert reconstruction['input'] == dict(source_execution_id='job_source', rig='rig_a', camera='front')


def test_real_source_import_sensitivity_preview_result_and_case_share_api():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.preview_neural_source_import('road_source', project_id='project_a')
        client.preview_neural_perception('job_render', model_id='model_a', model_revision=2, project_id='project_a')
        client.neural_perception_result('job_perception', project_id='project_a')
        client.capture_neural_perception_case('job_perception', project_id='project_a', frame_index=30,
            title='View sensitivity', note='Output comparison', idempotency_key='case')
    assert [(item.method, item.url.path) for item in requests] == [
        ('POST', '/api/v1/experiments/preview'), ('POST', '/api/v1/neural-sim/perception/preview'),
        ('GET', '/api/v1/neural-sim/perception/job_perception'), ('POST', '/api/v1/neural-sim/perception/job_perception/case')]
    assert json.loads(requests[0].content)['kind'] == 'neural_source_import'
    assert json.loads(requests[1].content)['frame_indices'] == [0, 15, 30, 45, 60]
    assert requests[3].headers['Idempotency-Key'] == 'case'


@pytest.mark.parametrize('changes', [dict(frame_indices=[0, 1]), dict(frame_indices=[0, 0, 1]),
    dict(frame_indices=[0, 1, True]), dict(frame_indices=[0, 1, 200]), dict(model_revision=0),
    dict(score_threshold=float('nan')), dict(max_detections=True)])
def test_invalid_sensitivity_selection_never_reaches_network(changes):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid input reached network'))) as client:
        with pytest.raises(ValueError):
            client.preview_neural_perception('job_render', **{'model_id': 'model_a', 'model_revision': 1, **changes})


def test_sweep_preset_uses_frozen_source_defaults_without_changing_standard_defaults():
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.preview_neural_sim_preset('sweep', expected_binding_revision=1)
        client.preview_neural_sim_preset('left', expected_binding_revision=1)
        client.preview_neural_sim_preset('sweep', expected_binding_revision=1, parameters={'lateral_m': 2}, frame_index=199)
    # An omitted source frame uses the server's source-specific preset default.
    assert requests[0]['mode'] == 'camera_sweep' and requests[0]['frame_index'] is None
    assert requests[1]['mode'] == 'sequence' and requests[1]['frame_index'] is None
    assert requests[2]['parameters']['lateral_m'] == 2 and requests[2]['frame_index'] == 199


@pytest.mark.parametrize('identity,mode,amplitude', [('sweep', 'camera_sweep', 0), ('sweep', 'camera_sweep', -.1),
    ('sweep', 'camera_sweep', 2.01), ('left', 'frame', 1.5), ('right', 'sequence', 1.5)])
def test_sweep_amplitude_is_positive_and_standard_bounds_stay_narrow(identity, mode, amplitude):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid input reached network'))) as client:
        with pytest.raises(ValueError):
            client.preview_neural_sim_preset(identity, expected_binding_revision=1, mode=mode, parameters={'lateral_m': amplitude})


@pytest.mark.parametrize('changes', [dict(parameters={'lateral_m': True}), dict(parameters={'height_m': float('nan')}),
    dict(parameters={'lateral_m': .6}), dict(parameters={'height_m': -.4}), dict(parameters={'yaw': 1}),
    dict(mode='video'), dict(frame_index=True), dict(frame_index=200), dict(expected_binding_revision=0)])
def test_invalid_preview_fails_before_network(changes):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid input reached network'))) as client:
        with pytest.raises(ValueError):
            client.preview_neural_sim_preset('left', **{'expected_binding_revision': 1, **changes})


@pytest.mark.parametrize('changes', [dict(expected_revision=True), dict(source_execution_id='../job'),
    dict(reconstruction_execution_id='../job'), dict(saved_execution_id='../job'), dict(seed=True), dict(project_id='../p')])
def test_invalid_binding_fails_before_network(changes):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid input reached network'))) as client:
        with pytest.raises(ValueError):
            client.bind_neural_sim_preset('left', **{'source_execution_id': 'job_source', **changes})


@pytest.mark.parametrize('drift', [False, True])
def test_explicit_render_submit_checks_preview_hash(drift):
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/executions'):
            return httpx.Response(202, json={'id': 'job_render', 'status': 'queued'})
        return httpx.Response(201, json={'id': 'experiment_render', 'preview_hash': ('b' if drift else 'a') * 64})
    preview = dict(schema_version='neural-sim-preset-preview/1',
        experiment=dict(name='Neural', kind='neural_render'), preview=dict(preview_hash='a' * 64))
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        if drift:
            with pytest.raises(ValueError, match='no execution was submitted'):
                client.execute_neural_sim_preview(preview, idempotency_key='explicit')
        else:
            assert client.execute_neural_sim_preview(preview, idempotency_key='explicit')['status'] == 'queued'
    assert len(requests) == (1 if drift else 2)
