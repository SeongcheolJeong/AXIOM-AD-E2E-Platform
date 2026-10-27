"""SDK contracts and recovery after submission; no native engine execution."""
import json
import httpx
import pytest
from axiom_sdk import AxiomClient
from axiom_sdk.hil_beta import HilExecutionLinkError

REF = {'id':'hil_test_one','revision':2,'sha256':'a'*64}
SAVE = dict(name='Delay study',preset_id='input_delay',binding_revision=1,binding_sha256='b'*64,
            parameters={'delay_ms':300},idempotency_key='save-one',project_id='project_one')


def test_saved_revision_and_observation_transport():
    requests=[]
    def respond(request):
        requests.append(request); return httpx.Response(200,json={'ok':True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.save_hil_sim_test(**SAVE,test_id='hil_test_one',expected_revision=1,copied_from=REF)
        client.hil_sim_test('hil_test_one',project_id='project_one',revision=2)
        client.preview_hil_sim_test(REF,project_id='project_one')
        client.attach_hil_sim_test_execution(REF,'job_one',project_id='project_one',idempotency_key='link-one')
        client.create_hil_sim_observation(execution_id='job_one',time_s=4.55,title='Stopped',note='B stopped',
            test_ref=REF,project_id='project_one',side='faulted',kind='issue',idempotency_key='note-one')
        client.hil_sim_observations(project_id='project_one',test_id=REF['id'],revision=2)
        client.export_hil_sim_test(REF['id'],project_id='project_one',revision=2)
    assert json.loads(requests[0].content)['copied_from']==REF
    assert 'dut_profiles' not in json.loads(requests[0].content)
    assert requests[1].url.params['revision']=='2'
    assert json.loads(requests[2].content)['test_ref']==REF
    assert requests[3].headers['Idempotency-Key']=='link-one'
    assert json.loads(requests[4].content)['time_s']==4.55
    assert requests[-1].url.path.endswith('/hil_test_one/export')
    assert requests[-1].url.params['project_id']=='project_one'


@pytest.mark.parametrize('preset_id,parameters', [
    ('input_delay', {'delay_ms': 300.0, 'start_s': 5.5, 'duration_s': 2.0}),
    ('input_dropout', {'duration_s': .5, 'start_s': 17}),
])
def test_save_timing_uses_shared_normalized_contract(preset_id, parameters):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(201, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.save_hil_sim_test(**{**SAVE, 'preset_id': preset_id, 'parameters': parameters})
    submitted = json.loads(requests[0].content)['parameters']
    assert submitted == parameters
    if preset_id == 'input_delay':
        assert type(submitted['delay_ms']) is int and type(submitted['duration_s']) is int


def test_dut_profile_crud_and_exact_test_refs_share_product_api():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    parameters = dict(max_object_age_ms=250, recovery_fresh_steps=3)
    profiles = dict(baseline=REF, faulted={**REF, 'id': 'hil_dut_b', 'sha256': 'c' * 64})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.hil_sim_dut_profiles(project_id='project_one', limit=10, offset=2)
        client.save_hil_sim_dut_profile(name='Conservative DUT', note='Recovery study', parameters=parameters,
            idempotency_key='profile-r2', project_id='project_one', profile_id='hil_dut_a', expected_revision=1)
        client.hil_sim_dut_profile('hil_dut_a', project_id='project_one', revision=2)
        client.save_hil_sim_test(**SAVE, dut_profiles=profiles)
    assert [(request.method, request.url.path) for request in requests] == [
        ('GET', '/api/v1/hil-sim/dut-profiles'), ('POST', '/api/v1/hil-sim/dut-profiles'),
        ('GET', '/api/v1/hil-sim/dut-profiles/hil_dut_a'), ('POST', '/api/v1/hil-sim/tests')]
    assert requests[0].url.params['offset'] == '2'
    assert requests[1].headers['Idempotency-Key'] == 'profile-r2'
    assert json.loads(requests[1].content) == dict(project_id='project_one', name='Conservative DUT', note='Recovery study',
        parameters=parameters, id='hil_dut_a', expected_revision=1)
    assert requests[2].url.params['revision'] == '2'
    assert json.loads(requests[3].content)['dut_profiles'] == profiles


@pytest.mark.parametrize('parameters', [
    dict(max_object_age_ms=True, recovery_fresh_steps=3), dict(max_object_age_ms=250., recovery_fresh_steps=3),
    dict(max_object_age_ms=float('nan'), recovery_fresh_steps=3), dict(max_object_age_ms=75, recovery_fresh_steps=3),
    dict(max_object_age_ms=1050, recovery_fresh_steps=3), dict(max_object_age_ms=250, recovery_fresh_steps=0),
    dict(max_object_age_ms=250, recovery_fresh_steps=11), dict(max_object_age_ms=250, recovery_fresh_steps=True),
    dict(max_object_age_ms=250, recovery_fresh_steps=3, other=1), {'max_object_age_ms': 250},
])
def test_invalid_dut_profile_values_never_reach_network(parameters):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Reached network'))) as client:
        with pytest.raises(ValueError):
            client.save_hil_sim_dut_profile(name='DUT', parameters=parameters, idempotency_key='profile')


@pytest.mark.parametrize('profiles', [dict(baseline=REF), dict(baseline=REF, faulted={**REF, 'revision': True}),
    dict(baseline=REF, faulted={**REF, 'sha256': 'incorrect'}),
    dict(baseline=REF, faulted={**REF, 'parameters': {}})])
def test_test_profile_refs_are_exact_before_network(profiles):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Reached network'))) as client:
        with pytest.raises(ValueError):
            client.save_hil_sim_test(**SAVE, dut_profiles=profiles)


@pytest.mark.parametrize('change',[{'parameters':{'delay_ms':True}},{'parameters':{'delay_ms':75}},
    {'parameters':{'delay_ms':float('nan')}},{'binding_sha256':'invalid'},
    {'expected_revision':True},{'name':'  '},{'test_id':'../test'},
    {'copied_from':{**REF,'revision':0}},{'idempotency_key':''},
    {'parameters': {'delay_ms': 300, 'start_s': 14}},
    {'parameters': {'delay_ms': 300, 'start_s': False}},
    {'parameters': {'delay_ms': 300, 'duration_s': float('inf')}},
    {'parameters': {'delay_ms': 300, 'duration_s': 2.25}},
    {'parameters': {'delay_ms': 300, 'start_s': 16.00000001, 'duration_s': .5}},
    {'parameters': {'delay_ms': 300, 'duration_s': 1.0000000001}},
    {'parameters': {'delay_ms': 300, 'end_s': 5}},
    {'parameters': {'start_s': 3}}])
def test_invalid_definition_fails_before_network(change):
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('Reached network'))) as client:
        with pytest.raises(ValueError):client.save_hil_sim_test(**{**SAVE,**change})


@pytest.mark.parametrize('time_s',[-.1,18.01,float('inf'),True])
def test_invalid_response_time(time_s):
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('Reached network'))) as client:
        with pytest.raises(ValueError):client.create_hil_sim_observation(execution_id='job_one',time_s=time_s,
            title='T',note='N',idempotency_key='note-one')


@pytest.mark.parametrize('link_failure',[False,True])
def test_execute_attaches_exact_revision_and_retains_execution_on_link_failure(link_failure):
    requests=[]
    def respond(request):
        requests.append(request)
        if '/hil-sim/tests/' in request.url.path:
            return httpx.Response(503 if link_failure else 201,json={'detail':'temporary','id':'link_one'})
        if request.url.path.endswith('/executions'):
            return httpx.Response(202,json={'id':'job_one','status':'queued'})
        return httpx.Response(201,json={'id':'experiment_one','preview_hash':'a'*64})
    preview=dict(schema_version='hil-sim-test-preview/1',test_ref=REF,
        experiment=dict(project_id='project_one',name='HIL',kind='hil_demo'),preview=dict(preview_hash='a'*64))
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        if link_failure:
            with pytest.raises(HilExecutionLinkError) as caught:
                client.execute_hil_sim_preview(preview,idempotency_key='same-intent')
            assert caught.value.execution_id=='job_one'
            assert caught.value.test_ref==REF
        else:
            assert client.execute_hil_sim_preview(preview,idempotency_key='same-intent')['id']=='job_one'
    assert len(requests)==3
    assert all(request.headers['Idempotency-Key']=='same-intent' for request in requests)
    assert json.loads(requests[2].content)['test_ref']==REF


def test_preview_drift_never_submits_or_attaches():
    requests=[]
    def respond(request):
        requests.append(request);return httpx.Response(201,json={'id':'experiment_one','preview_hash':'b'*64})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError,match='no execution was submitted'):
            client.execute_hil_sim_preview(dict(schema_version='hil-sim-test-preview/1',test_ref=REF,
                experiment=dict(project_id='project_one',name='HIL',kind='hil_demo'),preview=dict(preview_hash='a'*64)),
                idempotency_key='drift')
    assert len(requests)==1
