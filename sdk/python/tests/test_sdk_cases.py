import hashlib
import httpx
import pytest

from axiom_sdk import AxiomClient


def test_cases_use_same_rest_contract_and_explicit_request_key(tmp_path):
    calls = []
    raw = b'{"id":"case_1","revision":2}'
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/export'):
            return httpx.Response(200, content=raw)
        return httpx.Response(200, json={'id': 'case_1', 'revision': 2, 'manifest_hash': hashlib.sha256(raw).hexdigest(),
            'manifest_size_bytes': len(raw), 'download_url': 'https://untrusted.invalid/case.json'})
    with AxiomClient('http://api.test', transport=httpx.MockTransport(handler)) as api:
        api.create_case(title='Example', note='Observed failure, not a confirmed cause',
            source={'kind':'evaluation','evaluation_id':'eval_1','image_id':'img_1'}, idempotency_key='stable')
        assert calls[-1].headers['Idempotency-Key'] == 'stable'
        assert api.export_case('case_1', tmp_path/'case.json', revision=2).read_bytes() == raw
        assert all(r.url.host == 'api.test' for r in calls)
        assert calls[-1].url.params['revision'] == '2'


def test_case_sdk_rejects_paths_and_invalid_page_bounds_without_request():
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected request'))) as api:
        for identifier in ('../x','http://external.invalid','a/b'):
            with pytest.raises(ValueError): api.get_case(identifier)
        with pytest.raises(ValueError): api.cases(limit=0)
        with pytest.raises(ValueError): api.case_samples('case_1', offset=-1)
        with pytest.raises(ValueError): api.get_case('case_1', revision=True)


def test_case_sdk_lost_response_does_not_resubmit():
    calls=[]
    def handler(request):
        calls.append(request); raise httpx.ReadError('Response lost')
    with AxiomClient(transport=httpx.MockTransport(handler)) as api:
        with pytest.raises(httpx.ReadError):
            api.revise_case('case_1', idempotency_key='fixed', expected_revision=2,
                title='Review', note='Explicit investigation', state='investigating')
    assert len(calls)==1 and calls[0].headers['Idempotency-Key']=='fixed'
