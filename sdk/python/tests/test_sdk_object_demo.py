"""Object Sim demo transport contract, not a native simulation test."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_demo_methods_share_project_api_and_pins():
    requests=[]
    def respond(request):
        requests.append(request);return httpx.Response(200,json={'ok':True})
    ref={'id':'scenario2_a','revision':2,'configuration_hash':'a'*64,'manifest_hash':'b'*64}
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.object_sim_presets(project_id='p')
        client.bind_object_sim_preset('obstacle_bypass',ref,{'ego':'ego','obstacle':'parked'},
            project_id='p',expected_revision=1,saved_execution_id='job_a')
        client.preview_object_sim_preset('obstacle_bypass',project_id='p',parameters={'allow_lane_changes':False,'ego_speed_mps':3})
        client.object_sim_comparison('job_a','job_b',project_id='p')
    assert [(r.method,r.url.path) for r in requests]==[
        ('GET','/api/v1/object-sim/presets'),('POST','/api/v1/object-sim/presets/obstacle_bypass/bind'),
        ('POST','/api/v1/object-sim/presets/obstacle_bypass/preview'),('GET','/api/v1/object-sim/comparison')]
    assert json.loads(requests[1].content)['scenario_ref']==ref
    assert json.loads(requests[2].content)['parameters']['allow_lane_changes'] is False
    assert dict(requests[3].url.params)=={'project_id':'p','left_execution_id':'job_a','right_execution_id':'job_b'}


@pytest.mark.parametrize('kwargs',[{'parameters':{'ego_speed_mps':13}}, {'parameters':{'ego_speed_mps':True}},
    {'parameters':{'allow_lane_changes':1}}, {'parameters':{'rear_gap_m':10}}, {'parameters':{'ego_speed_mps':float('nan')}},
    {'actor_poses':{'ego':{'x':1,'y':2}}}, {'actor_poses':{'../actor':{'x':1,'y':2,'z':0,'heading_rad':0}}}])
def test_invalid_edit_fails_before_network(kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('invalid request reached network'))) as client:
        with pytest.raises(ValueError):client.preview_object_sim_preset('obstacle_bypass',**kwargs)


def test_urban_preset_uses_same_binding_and_speed_only_preview_contract():
    requests = []
    def respond(request):
        requests.append(request); return httpx.Response(200, json={'ok': True})
    ref = {'id': 'scenario2_urban', 'revision': 1, 'configuration_hash': 'a' * 64, 'manifest_hash': 'b' * 64}
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.bind_object_sim_preset('urban_city', ref, {'ego': 'ego'}, project_id='p')
        client.preview_object_sim_preset('urban_city', project_id='p', parameters={'ego_speed_mps': 3})
        with pytest.raises(ValueError, match='Unsupported parameter'):
            client.preview_object_sim_preset('urban_city', parameters={'allow_lane_changes': True})
    assert [(r.method, r.url.path) for r in requests] == [
        ('POST', '/api/v1/object-sim/presets/urban_city/bind'),
        ('POST', '/api/v1/object-sim/presets/urban_city/preview')]
    assert json.loads(requests[0].content)['scenario_ref'] == ref
    assert json.loads(requests[1].content) == {'project_id': 'p', 'parameters': {'ego_speed_mps': 3}, 'actor_poses': {}}


def test_interaction_preset_retains_saved_pair_and_wait_only_edit():
    requests = []
    def respond(request):
        requests.append(request); return httpx.Response(200, json={'ok': True})
    ref = {'id': 'scenario2_turn', 'revision': 1, 'configuration_hash': 'a' * 64, 'manifest_hash': 'b' * 64}
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.bind_object_sim_preset('urban_right_turn', ref, {'ego': 'ego', 'lead': 'lead'},
            saved_execution_id='job_b', saved_comparison_execution_id='job_a')
        client.preview_object_sim_preset('urban_right_turn', parameters={'lead_wait_s': 0})
        for params in ({'lead_wait_s': 7}, {'lead_wait_s': True}, {'ego_speed_mps': 4}):
            with pytest.raises(ValueError):
                client.preview_object_sim_preset('urban_right_turn', parameters=params)
    assert len(requests) == 2
    assert json.loads(requests[0].content)['saved_comparison_execution_id'] == 'job_a'
    assert json.loads(requests[1].content)['parameters'] == {'lead_wait_s': 0}
