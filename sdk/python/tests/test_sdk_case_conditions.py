import hashlib
import json

import httpx
import pytest
from axiom_sdk import AxiomClient


def test_sdk_filters_repeat_query_keys_and_explicit_revision():
    calls = []
    def handle(request):
        calls.append(request); return httpx.Response(200, json={'id': 'case_a', 'revision': 2})
    with AxiomClient('http://api.test', transport=httpx.MockTransport(handle)) as api:
        api.case_condition_taxonomy()
        api.cases(conditions=['lighting:night', 'target:pedestrian'], review='reviewed', diagnostic='has_false_negative')
        assert calls[-1].url.params.get_list('condition') == ['lighting:night', 'target:pedestrian']
        api.classify_case('case_a', idempotency_key='fixed', expected_revision=1, values={'lighting': 'night'})
        assert calls[-1].headers['Idempotency-Key'] == 'fixed'
        assert json.loads(calls[-1].content)['expected_revision'] == 1


def test_coverage_export_never_follows_external_url_and_validates_bytes(tmp_path):
    raw = b'{"schema_version":"test-fixture","cases":[]}'
    sha = hashlib.sha256(raw).hexdigest(); calls=[]
    def handle(request):
        calls.append(request)
        if request.url.path.endswith('/export'):
            assert request.url.params['expected_hash'] == sha
            return httpx.Response(200, content=raw)
        return httpx.Response(200, json={'manifest_hash': sha, 'manifest_size_bytes': len(raw), 'download_url': 'https://other.invalid/leak'})
    with AxiomClient('http://api.test', transport=httpx.MockTransport(handle)) as api:
        assert api.export_case_condition_coverage(tmp_path/'coverage.json', recording_id='rec_a').read_bytes() == raw
    assert all(r.url.host == 'api.test' and r.url.params['recording_id'] == 'rec_a' for r in calls)


def test_invalid_input_does_not_send_and_failed_write_not_retried():
    calls=[]
    def handle(request):
        calls.append(request); raise httpx.ReadError('Lost after commit')
    with AxiomClient(transport=httpx.MockTransport(handle)) as api:
        with pytest.raises(ValueError): api.case_condition_coverage(recording_id='../x')
        with pytest.raises(ValueError): api.cases(conditions=['broken'])
        with pytest.raises(ValueError): api.cases(review='approved')
        with pytest.raises(ValueError): api.classify_case('case_a', idempotency_key='fixed', expected_revision=True)
        assert calls == []
        with pytest.raises(httpx.ReadError): api.classify_case('case_a', idempotency_key='fixed', expected_revision=1)
    assert len(calls) == 1
