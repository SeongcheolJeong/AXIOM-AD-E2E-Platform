import hashlib
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_explicit_retest_write_uses_same_key_once():
    calls=[]
    def send(request):
        calls.append(request);raise httpx.ReadError('Lost response')
    with AxiomClient(transport=httpx.MockTransport(send)) as client:
        with pytest.raises(httpx.ReadError):
            client.submit_case_retest('case_1','retest_1',expected_revision=3,plan_hash='a'*64,idempotency_key='one')
    assert len(calls)==1 and calls[0].url.path=='/api/v1/cases/case_1/retests/retest_1/campaigns'
    assert calls[0].headers['Idempotency-Key']=='one'


def test_retest_export_checks_origin_size_and_hash(tmp_path):
    raw=b'{"case_id":"case_1","case_revision":4}';calls=[]
    def send(request):
        calls.append(request)
        if request.url.path.endswith('/export'):return httpx.Response(200,content=raw)
        return httpx.Response(200,json={'case_id':'case_1','case_revision':4,'manifest_hash':hashlib.sha256(raw).hexdigest(),
            'manifest_size_bytes':len(raw),'download_url':'https://outside.invalid/credentials'})
    with AxiomClient('https://internal.test',transport=httpx.MockTransport(send)) as client:
        assert client.export_case_retests('case_1',tmp_path/'result.json',revision=4).read_bytes()==raw
    assert all(r.url.host=='internal.test' for r in calls)
    assert calls[-1].url.params['expected_hash']==hashlib.sha256(raw).hexdigest()


def test_bad_identity_and_revision_never_send():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('Unexpected HTTP'))) as client:
        with pytest.raises(ValueError):client.case_retests('../other')
        with pytest.raises(ValueError):client.case_retests('case_1',revision=True)
        with pytest.raises(ValueError):client.submit_case_retest('case_1','https://outside.test',expected_revision=1,plan_hash='a'*64,idempotency_key='x')
        with pytest.raises(ValueError):client.create_case_retest('case_1',expected_revision=False,idempotency_key='x')
