import json
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_same_api_explicit_revision_and_submissions():
    requests=[]
    def respond(request):
        requests.append(request);return httpx.Response(200,json={'id':'vp_test'})
    with AxiomClient('http://workspace.test',transport=httpx.MockTransport(respond)) as client:
        client.create_verification_plan(idempotency_key='create',name='Workflow')
        client.preview_verification('vp_test',revision=2)
        assert json.loads(requests[-1].content)=={'revision':2}
        assert 'Idempotency-Key' not in requests[-1].headers
        client.submit_verification('vp_test',revision=2,expected_preview_hash='a'*64,confirm_trusted_models=True,idempotency_key='execute')
        assert requests[-1].headers['Idempotency-Key']=='execute'
        client.verification_attempt('va_test')
        assert requests[-1].method=='GET'
        client.cancel_verification('va_test')
        assert requests[-1].url.path=='/api/v1/verification-attempts/va_test/cancel'
    assert all(r.url.host=='workspace.test' for r in requests)


def test_invalid_selection_starts_no_requests():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('unexpected request'))) as client:
        with pytest.raises(ValueError):client.verification_plan('../bad')
        with pytest.raises(ValueError):client.preview_verification('vp_test',revision=True)
        with pytest.raises(ValueError):client.submit_verification('vp_test',revision=1,expected_preview_hash='a'*64,confirm_trusted_models=False,idempotency_key='execute')


def test_hash_verified_export_and_retry_preview(tmp_path):
    import hashlib
    raw=b'{"schema_version":"verification-attempt/1"}'
    requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(200,content=raw) if request.url.path.endswith('/export') else httpx.Response(200,json={'manifest_hash':hashlib.sha256(raw).hexdigest()})
    with AxiomClient('http://workspace.test',transport=httpx.MockTransport(respond)) as client:
        client.preview_verification('vp_test',revision=2,retry_of='va_previous')
        assert json.loads(requests[-1].content)=={'revision':2,'retry_of':'va_previous'}
        assert client.export_verification_attempt('va_test',tmp_path/'receipt.json').read_bytes()==raw
        assert client.export_verification_plan('vp_test',tmp_path/'plan.json',revision=2).read_bytes()==raw
    assert all(r.url.host=='workspace.test' for r in requests)


@pytest.mark.parametrize('mode',['unsealed','changed_bytes'])
def test_invalid_export_never_overwrites_existing_evidence(tmp_path,mode):
    target=tmp_path/'receipt.json';target.write_bytes(b'preserved evidence')
    def respond(request):
        if request.url.path.endswith('/export'):
            return httpx.Response(200,content=b'tampered bytes')
        return httpx.Response(200,json={'manifest_hash':None if mode=='unsealed' else 'a'*64})
    with AxiomClient('http://workspace.test',transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError):client.export_verification_attempt('va_test',target)
    assert target.read_bytes()==b'preserved evidence'
