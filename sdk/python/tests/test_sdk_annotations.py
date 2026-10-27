import hashlib
import httpx
import pytest
from axiom_sdk import AxiomClient


def test_explicit_workload_publish_and_fixed_origin_download(tmp_path):
    calls=[];raw=b'{"id":"annotation_1"}'
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/export'):return httpx.Response(200,content=raw)
        return httpx.Response(200,json={'id':'annotation_1','manifest_hash':hashlib.sha256(raw).hexdigest(),
            'manifest_size_bytes':len(raw),'download_url':'https://other.invalid/ignore',
            'dataset':{'id':'dataset_1','revision':1}})
    with AxiomClient('http://test',transport=httpx.MockTransport(respond)) as api:
        api.preview_annotation_task('dataset_1',expected_revision=1)
        assert 'Idempotency-Key' not in calls[-1].headers
        api.create_annotation_task('dataset_1',expected_revision=1,confirm_trusted_model=True,idempotency_key='intent')
        assert calls[-1].headers['Idempotency-Key']=='intent'
        api.publish_annotation_drafts('annotation_1',expected_revision=1,idempotency_key='publish')
        api.annotation_lineage('dataset_1',revision=1)
        assert api.export_annotation_report('annotation_1',tmp_path/'report.json').read_bytes()==raw
        assert all(r.url.host=='test' for r in calls)


def test_invalid_identifiers_revision_and_missing_consent_never_call_network():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('Unexpected request'))) as api:
        with pytest.raises(ValueError):api.annotation_task('../escape')
        with pytest.raises(ValueError):api.preview_annotation_task('dataset_1',expected_revision=True)
        with pytest.raises(ValueError):api.create_annotation_task('dataset_1',expected_revision=1,idempotency_key='x')


def test_response_loss_is_not_automatically_retried():
    calls=[]
    def respond(request):calls.append(request);raise httpx.ReadError('Response lost')
    with AxiomClient(transport=httpx.MockTransport(respond)) as api:
        with pytest.raises(httpx.ReadError):api.publish_annotation_drafts('annotation_1',expected_revision=1,idempotency_key='retain')
    assert len(calls)==1
