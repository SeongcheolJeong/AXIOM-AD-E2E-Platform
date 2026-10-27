import hashlib
import httpx
import pytest

from axiom_sdk import AxiomClient


def test_explicit_submission_and_verified_same_server_download(tmp_path):
    raw=b'{"case_id":"case_1","case_revision":3}'
    calls=[]
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/export'):return httpx.Response(200,content=raw)
        return httpx.Response(200,json={'case_id':'case_1','case_revision':3,
            'manifest_hash':hashlib.sha256(raw).hexdigest(),'manifest_size_bytes':len(raw),
            'download_url':'https://untrusted.invalid/not-used'})
    with AxiomClient('http://api.test',transport=httpx.MockTransport(respond)) as api:
        api.preview_case_stack_replay('case_1',expected_revision=1,baseline_replay_id='stack_replay_1')
        assert 'Idempotency-Key' not in calls[-1].headers
        api.submit_case_stack_replay('case_1',expected_revision=1,idempotency_key='stable',confirm_open_loop=True)
        assert calls[-1].headers['Idempotency-Key']=='stable'
        api.capture_case_stack_replay('case_1','csreplay_1',expected_revision=2,plan_hash='0'*64,idempotency_key='capture')
        assert api.export_case_stack_replays('case_1',tmp_path/'report.json',revision=3).read_bytes()==raw
        assert all(c.url.host=='api.test' for c in calls)


def test_no_request_for_bad_ids_bounds_or_missing_consent():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('Unexpected request'))) as api:
        with pytest.raises(ValueError):api.case_stack_replays('../other')
        with pytest.raises(ValueError):api.case_stack_replays('case_1',revision=True)
        with pytest.raises(ValueError):api.submit_case_stack_replay('case_1',expected_revision=1,idempotency_key='key')
        with pytest.raises(ValueError):api.capture_case_stack_replay('case_1','a/b',expected_revision=2,idempotency_key='key')


def test_response_loss_is_not_resubmitted():
    calls=[]
    def fail(request):calls.append(request);raise httpx.ReadError('Response lost')
    with AxiomClient(transport=httpx.MockTransport(fail)) as api:
        with pytest.raises(httpx.ReadError):api.submit_case_stack_replay('case_1',expected_revision=1,idempotency_key='stable',confirm_open_loop=True)
    assert len(calls)==1
