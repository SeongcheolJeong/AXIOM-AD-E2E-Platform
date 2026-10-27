"""Object beta transport contract, not a simulation or browser check."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient

REF = {'id': 'scenario_a', 'revision': 1, 'configuration_hash': 'a' * 64, 'manifest_hash': 'b' * 64}


def test_beta_preview_and_observation_share_pins_and_versioned_api():
    requests = []
    def respond(request):
        requests.append(request); return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.bind_object_sim_preset('cut_in', REF, {'ego': 'ego', 'intruder': 'car'})
        client.preview_object_sim_preset('cut_in', parameters={'cut_in_start_s': 3},
            actor_edits={'car': {'speed_mps': 5}}, source_scenario_ref=REF)
        client.create_object_sim_observation('job_a', REF, time_s=5, title='Observed', note='Ego stopped',
            behavior_exercised='observed', ego_outcome='stopped', idempotency_key='note-1')
        client.object_sim_observations(execution_id='job_a', limit=10)
        client.object_sim_behavior_report('job_a', project_id='p')
    assert [request.url.path for request in requests] == [
        '/api/v1/object-sim/presets/cut_in/bind', '/api/v1/object-sim/presets/cut_in/preview',
        '/api/v1/object-sim/observations', '/api/v1/object-sim/observations', '/api/v1/object-sim/executions/job_a/behavior']
    assert json.loads(requests[1].content)['source_scenario_ref'] == REF
    assert json.loads(requests[2].content)['ego_outcome'] == 'stopped'
    assert requests[2].headers['Idempotency-Key'] == 'note-1'


@pytest.mark.parametrize('edits', [{'ego': {'speed_mps': True}}, {'ego': {'speed_mps': 13}},
    {'ego': {'destination': {'x': 0}}}, {'ego': {'risk_mode': True}}, {'ego': {'actions': None}},
    {'ego': {'actions': [{'id': 'wait', 'kind': 'wait', 'duration_s': -1}]}}])
def test_invalid_beta_actor_edit_does_not_reach_network(edits):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('invalid edit reached network'))) as client:
        with pytest.raises(ValueError):
            client.preview_object_sim_preset('lead_stop_start', actor_edits=edits)


@pytest.mark.parametrize('fields', [{'time_s': float('nan')}, {'time_s': True}, {'ego_outcome': 'safe'},
    {'note': 'Bearer secret'}, {'note': ''}, {'behavior_exercised': 'passed'}])
def test_invalid_observation_does_not_reach_network(fields):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('invalid observation reached network'))) as client:
        with pytest.raises(ValueError):
            client.create_object_sim_observation('job_a', REF, **{'time_s': 1, 'title': 'Title', 'note': 'Note',
                'idempotency_key': 'n', **fields})
