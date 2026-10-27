import json
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_batch_shared_product_api_exact_pins_and_explicit_keys():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.log_sim_library(project_id='demo', library_id='lib_a')
        client.preview_log_sim_batch(['source_a'], project_id='demo', library_id='lib_a', selected_scene_ids=['scene_a'])
        client.submit_log_sim_batch(['source_a'], project_id='demo', library_id='lib_a', selected_scene_ids=['scene_a'],
            preview_hash='a'*64, idempotency_key='submit')
        client.log_sim_batches(project_id='demo')
        client.log_sim_batch('batch_a', project_id='demo')
        client.retry_log_sim_batch('batch_a', project_id='demo', idempotency_key='retry')
        client.save_log_sim_case_bundle('batch_a', project_id='demo', name='Review', candidate_ids=['candidate_a'],
            report_hash='b'*64, idempotency_key='bundle')
        client.log_sim_case_bundles(project_id='demo')
        client.log_sim_case_bundle('bundle_a', project_id='demo')
    assert [r.url.path for r in requests] == ['/api/v1/log-sim/library', '/api/v1/log-sim/batches/preview',
        '/api/v1/log-sim/batches', '/api/v1/log-sim/batches', '/api/v1/log-sim/batches/batch_a',
        '/api/v1/log-sim/batches/batch_a/retry', '/api/v1/log-sim/batches/batch_a/case-bundles',
        '/api/v1/log-sim/case-bundles', '/api/v1/log-sim/case-bundles/bundle_a']
    assert json.loads(requests[2].content) == json.loads(requests[1].content) | {'preview_hash': 'a'*64}
    assert requests[2].headers['Idempotency-Key'] == 'submit'
    assert requests[5].headers['Idempotency-Key'] == 'retry'
    assert json.loads(requests[6].content)['report_hash'] == 'b'*64


@pytest.mark.parametrize('source_ids,extra', [([], {}), (['a', 'a'], {}), (['../foreign'], {}),
    (['a'], {'selected_scene_ids': ['scene_a']}), (['a'], {'library_id': 'lib_a', 'selected_scene_ids': ['x', 'x']})])
def test_batch_rejects_invalid_selection_before_transport(source_ids, extra):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected transport'))) as client:
        with pytest.raises(ValueError):
            client.preview_log_sim_batch(source_ids, **extra)


def test_bundle_hash_and_duplicate_candidates_rejected_before_transport():
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected transport'))) as client:
        for values in ({'candidate_ids': ['x', 'x'], 'report_hash': 'a'*64}, {'candidate_ids': ['x'], 'report_hash': 'bad'}):
            with pytest.raises(ValueError):
                client.save_log_sim_case_bundle('batch_a', name='Review', idempotency_key='save', **values)
