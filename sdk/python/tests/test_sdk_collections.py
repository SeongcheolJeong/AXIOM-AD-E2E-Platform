import hashlib
import httpx
import pytest

from axiom_sdk import AxiomClient


def test_explicit_writes_and_same_origin_hash_checked_exports(tmp_path):
    requests=[];raw=b'{"id":"collection_1","revision":2}'
    def reply(request):
        requests.append(request)
        if request.url.path.endswith('/export'):return httpx.Response(200,content=raw)
        identifier='receipt_1' if '/collection-datasets/' in request.url.path else 'collection_1'
        return httpx.Response(200,json={'id':identifier,'revision':2,'manifest_hash':hashlib.sha256(raw).hexdigest(),
            'manifest_size_bytes':len(raw),'download_url':'https://untrusted.invalid/never-follow'})
    with AxiomClient('http://test.local',transport=httpx.MockTransport(reply)) as api:
        api.preview_collection(name='Data',sources=[])
        assert 'Idempotency-Key' not in requests[-1].headers
        api.create_collection(idempotency_key='source-key',name='Data')
        assert requests[-1].headers['Idempotency-Key']=='source-key'
        api.revise_collection('collection_1',expected_revision=1,idempotency_key='revise')
        api.preview_collection_dataset('collection_1',collection_revision=2)
        api.publish_collection_dataset('collection_1',collection_revision=2,idempotency_key='publish')
        assert api.export_collection('collection_1',tmp_path/'collection.json',revision=2).read_bytes()==raw
        assert api.export_collection_dataset_receipt('receipt_1',tmp_path/'receipt.json').read_bytes()==raw
        assert all(r.url.host=='test.local' for r in requests)


def test_invalid_identifiers_revisions_and_scopes_do_not_send_requests():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('Unexpected request'))) as api:
        with pytest.raises(ValueError):api.get_collection('../escape')
        with pytest.raises(ValueError):api.get_collection('collection_1',revision=True)
        with pytest.raises(ValueError):api.collections(limit=False)
        with pytest.raises(ValueError):api.collection_dataset_publications()
        with pytest.raises(ValueError):api.publish_collection_dataset('collection_1',collection_revision=0,idempotency_key='publish')
        with pytest.raises(ValueError):api.create_collection(idempotency_key='')


def test_response_loss_never_automatically_republishes():
    requests=[]
    def reply(request):requests.append(request);raise httpx.ReadError('Lost response')
    with AxiomClient(transport=httpx.MockTransport(reply)) as api:
        with pytest.raises(httpx.ReadError):api.create_collection(idempotency_key='same-key',name='Dataset')
    assert len(requests)==1
