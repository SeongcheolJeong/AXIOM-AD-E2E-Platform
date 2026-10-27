import hashlib
import httpx
import pytest

from axiom_sdk import AxiomClient


def test_explicit_replay_actions_and_same_origin_export(tmp_path):
    raw = b'{"case_id":"case_1","case_revision":4}'
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/export'):
            return httpx.Response(200, content=raw)
        return httpx.Response(200, json={'case_id': 'case_1', 'case_revision': 4,
            'manifest_hash': hashlib.sha256(raw).hexdigest(), 'manifest_size_bytes': len(raw),
            'download_url': 'https://untrusted.invalid/do-not-use'})
    with AxiomClient('http://api.test', transport=httpx.MockTransport(respond)) as api:
        api.preview_case_model_replay('case_1', expected_revision=1, baseline_evaluation_id='eval_1', model_id='model_1', model_revision=1, note='Review')
        assert requests[-1].url.path.endswith('/preview') and 'Idempotency-Key' not in requests[-1].headers
        api.submit_case_model_replay('case_1', expected_revision=1, confirm_trusted_model=True,
            expected_preview_hash='0'*64, idempotency_key='explicit-submit')
        assert requests[-1].headers['Idempotency-Key'] == 'explicit-submit'
        api.evaluate_case_model_replay('case_1', 'modelreplay_1', expected_revision=2, plan_hash='0'*64, idempotency_key='eval')
        api.capture_case_model_replay('case_1', 'modelreplay_1', expected_revision=3, plan_hash='0'*64, idempotency_key='capture')
        assert api.export_case_model_replays('case_1', tmp_path/'report.json', revision=4).read_bytes() == raw
        assert all(r.url.host == 'api.test' for r in requests)
        assert requests[-1].url.params['revision'] == '4'


def test_invalid_identity_and_missing_consent_never_send_requests():
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected HTTP call'))) as api:
        with pytest.raises(ValueError): api.case_model_replays('../outside')
        with pytest.raises(ValueError): api.case_model_replays('case_1', revision=True)
        with pytest.raises(ValueError): api.submit_case_model_replay('case_1', expected_revision=1, idempotency_key='x')


def test_response_loss_is_not_automatically_resubmitted():
    calls = []
    def respond(request):
        calls.append(request); raise httpx.ReadError('Response lost')
    with AxiomClient(transport=httpx.MockTransport(respond)) as api:
        with pytest.raises(httpx.ReadError):
            api.evaluate_case_model_replay('case_1', 'plan_1', expected_revision=2, plan_hash='0'*64, idempotency_key='retained-key')
    assert len(calls) == 1
