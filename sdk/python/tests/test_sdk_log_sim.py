import json
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_log_sim_shared_api_and_explicit_submission():
    requests=[]
    def respond(request):
        requests.append(request);return httpx.Response(200,json={'ok':True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.log_sim_sources(project_id='demo')
        client.log_sim_source('carla-urban-right-turn',project_id='demo')
        client.create_log_sim_case('carla-urban-right-turn',project_id='demo',title='wait',note='observed',
            start_time_s=2,end_time_s=9,idempotency_key='case')
        client.preview_log_sim_variant('carla-urban-right-turn',project_id='demo',lead_wait_s=2,case_id='case_a',case_revision=1)
        client.submit_log_sim_variant('carla-urban-right-turn',project_id='demo',lead_wait_s=2,
            case_id='case_a',case_revision=1,preview_hash='a'*64,idempotency_key='run')
        client.log_sim_variants(project_id='demo',source_id='carla-urban-right-turn')
    assert [r.url.path for r in requests]==['/api/v1/log-sim/sources','/api/v1/log-sim/sources/carla-urban-right-turn',
        '/api/v1/log-sim/sources/carla-urban-right-turn/cases','/api/v1/log-sim/variants/preview','/api/v1/log-sim/variants','/api/v1/log-sim/variants']
    assert json.loads(requests[4].content)['preview_hash']=='a'*64
    assert requests[4].headers['Idempotency-Key']=='run'


@pytest.mark.parametrize('kwargs',[{'lead_wait_s':True},{'lead_wait_s':7},{'lead_wait_s':float('nan')},
    {'lead_wait_s':2,'case_id':'case_a'},{'lead_wait_s':2,'case_id':'case_a','case_revision':False}])
def test_invalid_variant_rejected_before_transport(kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('Unexpected transport'))) as client:
        with pytest.raises(ValueError):client.preview_log_sim_variant('source',**kwargs)


def test_event_search_uses_shared_api_and_bounded_query():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'items': [], 'total': 0})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.log_sim_events('nuscenes-source', project_id='demo', q='감속', kind='deceleration',
            start_time_s=1, end_time_s=4, offset=0, limit=20)
    request = requests[0]
    assert request.url.path == '/api/v1/log-sim/sources/nuscenes-source/events'
    assert request.url.params['q'] == '감속' and request.url.params['kind'] == 'deceleration'
    assert request.url.params['start_time_s'] == '1' and request.url.params['limit'] == '20'


@pytest.mark.parametrize('kwargs', [{'q': 'x'*121}, {'kind': 'collision'}, {'limit': 101}, {'offset': True},
    {'start_time_s': float('nan')}, {'end_time_s': -1}, {'start_time_s': 2, 'end_time_s': 1}])
def test_invalid_event_query_rejected_before_transport(kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected transport'))) as client:
        with pytest.raises(ValueError):
            client.log_sim_events('source', **kwargs)


def test_comparison_and_regression_share_product_routes_and_pins():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.log_sim_replay_profiles(project_id='demo')
        client.preview_log_sim_comparison('source', case_id='case_a', case_revision=2, project_id='demo',
            candidate_profile='responsive')
        client.submit_log_sim_comparison('source', case_id='case_a', case_revision=2, project_id='demo',
            candidate_profile='responsive', preview_hash='f'*64, idempotency_key='compare')
        client.log_sim_comparison('logcmp_a', project_id='demo')
        client.log_sim_comparisons(project_id='demo', source_id='source')
        client.save_log_sim_regression('logcmp_a', name='Review case', project_id='demo',
            max_deceleration_request_mps2=4., max_target_speed_mps=8., idempotency_key='save')
        client.run_log_sim_regression('logreg_a', project_id='demo', idempotency_key='repeat')
        client.log_sim_regressions(project_id='demo', source_id='source')
    assert [r.url.path for r in requests] == ['/api/v1/log-sim/replay-profiles', '/api/v1/log-sim/comparisons/preview',
        '/api/v1/log-sim/comparisons', '/api/v1/log-sim/comparisons/logcmp_a', '/api/v1/log-sim/comparisons',
        '/api/v1/log-sim/comparisons/logcmp_a/regressions', '/api/v1/log-sim/regressions/logreg_a/runs',
        '/api/v1/log-sim/regressions']
    body = json.loads(requests[2].content)
    assert body == json.loads(requests[1].content) | {'preview_hash': 'f'*64}
    assert body['case_revision'] == 2 and body['candidate_profile'] == 'responsive'
    assert requests[2].headers['Idempotency-Key'] == 'compare'
    assert json.loads(requests[5].content)['max_deceleration_request_mps2'] == 4.
    assert requests[6].headers['Idempotency-Key'] == 'repeat'
    assert requests[7].url.params['source_id'] == 'source'


@pytest.mark.parametrize('kwargs', [{'case_revision': True}, {'case_revision': 0}, {'baseline_profile': 'learned'},
    {'candidate_profile': None}, {'case_id': '../case'}])
def test_comparison_rejects_invalid_pins_profiles_before_transport(kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected transport'))) as client:
        options = {'case_id': 'case_a', 'case_revision': 1} | kwargs
        with pytest.raises(ValueError):
            client.preview_log_sim_comparison('source', **options)


@pytest.mark.parametrize('kwargs', [{'name': ''}, {'name': 'x'*181}, {'max_deceleration_request_mps2': True},
    {'max_deceleration_request_mps2': 16}, {'max_target_speed_mps': float('inf')}, {'max_target_speed_mps': -1}])
def test_regression_rejects_invalid_limits_before_transport(kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected transport'))) as client:
        with pytest.raises(ValueError):
            client.save_log_sim_regression('logcmp_a', **({'name': 'Review', 'idempotency_key': 'save'} | kwargs))


def test_comparison_requires_exact_hash_before_submission():
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected transport'))) as client:
        with pytest.raises(ValueError):
            client.submit_log_sim_comparison('source', case_id='case_a', case_revision=1,
                preview_hash='invalid', idempotency_key='compare')
