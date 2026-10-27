"""Shared HIL API transport checks; no native engine or DUT is executed."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient

REF = dict(id='scene_1', revision=1, configuration_hash='a' * 64, manifest_hash='b' * 64)


@pytest.mark.parametrize('identity,parameters', [('input_delay', {'delay_ms': 500}), ('input_dropout', {'duration_s': 1.5}),
    ('input_delay', {'delay_ms': 300, 'start_s': 5.5, 'duration_s': 2}), ('input_delay', {'start_s': 1}),
    ('input_delay', {'duration_s': .5}), ('input_dropout', {'duration_s': .5, 'start_s': 17})])
def test_shared_api_transport(identity, parameters):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.hil_sim_presets(project_id='p')
        client.bind_hil_sim_preset(identity, project_id='p', scenario_ref=REF, expected_revision=1, saved_execution_id='job_a')
        client.preview_hil_sim_preset(identity, project_id='p', expected_binding_revision=2, parameters=parameters)
    assert [(request.method, request.url.path) for request in requests] == [
        ('GET', '/api/v1/hil-sim/presets'), ('POST', f'/api/v1/hil-sim/presets/{identity}/bindings'),
        ('POST', f'/api/v1/hil-sim/presets/{identity}/preview')]
    assert json.loads(requests[1].content)['scenario_ref'] == REF
    assert json.loads(requests[2].content)['parameters'] == parameters


@pytest.mark.parametrize('identity,parameters', [('missing', {}), ('input_delay', {'delay_ms': True}),
    ('input_delay', {'delay_ms': 75}), ('input_delay', {'delay_ms': float('nan')}),
    ('input_delay', {'delay_ms': 1050}), ('input_dropout', {'duration_s': .25}),
    ('input_dropout', {'delay_ms': 300}), ('input_delay', {'start_s': True}),
    ('input_delay', {'start_s': '3'}), ('input_delay', {'start_s': float('inf')}),
    ('input_delay', {'duration_s': float('nan')}), ('input_delay', {'start_s': .5}),
    ('input_delay', {'duration_s': 2.25}), ('input_delay', {'start_s': 17.5}),
    ('input_delay', {'start_s': 14}), ('input_dropout', {'start_s': 17}),
    ('input_delay', {'end_s': 7}),
    ('input_delay', {'start_s': 16.00000001, 'duration_s': .5}),
    ('input_delay', {'start_s': 3.000000001}),
    ('input_delay', {'duration_s': 1.0000000001}),
    ('input_dropout', {'duration_s': 1.0000000001})])
def test_invalid_parameters_fail_before_network(identity, parameters):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid request reached network'))) as client:
        with pytest.raises(ValueError):
            client.preview_hil_sim_preset(identity, expected_binding_revision=1, parameters=parameters)


@pytest.mark.parametrize('kwargs', [{'expected_revision': True}, {'seed': True}, {'seed': -1},
    {'project_id': '../p'}, {'scenario_ref': {}}, {'scenario_ref': {**REF, 'manifest_hash': 'bad'}},
    {'saved_execution_id': '../job'}])
def test_invalid_binding_fails_before_network(kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Invalid request reached network'))) as client:
        with pytest.raises(ValueError):
            client.bind_hil_sim_preset('input_delay', **{'scenario_ref': REF, **kwargs})


@pytest.mark.parametrize('drift', [False, True])
def test_explicit_submit_and_drift_guard(drift):
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/executions'):
            return httpx.Response(202, json={'id': 'job_one', 'status': 'queued'})
        return httpx.Response(201, json={'id': 'experiment_one', 'preview_hash': ('b' if drift else 'a') * 64})
    preview = dict(schema_version='hil-sim-preset-preview/1', experiment=dict(name='DUT', kind='hil_demo'),
                   preview=dict(preview_hash='a' * 64))
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        if drift:
            with pytest.raises(ValueError, match='no execution was submitted'):
                client.execute_hil_sim_preview(preview, idempotency_key='explicit')
        else:
            assert client.execute_hil_sim_preview(preview, idempotency_key='explicit')['status'] == 'queued'
    assert len(requests) == (1 if drift else 2)
    assert all(request.headers['Idempotency-Key'] == 'explicit' for request in requests)
