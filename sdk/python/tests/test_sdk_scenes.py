import hashlib
import json

import httpx
import pytest

from axiom_sdk import AxiomClient


def test_explicit_scene_actions_and_same_server_download(tmp_path):
    raw=b'{"index_id":"scene_1","rows":[]}'
    requests=[]
    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/export'):return httpx.Response(200,content=raw)
        return httpx.Response(200,json={'id':'scene_1','index_id':'scene_1','query':{'review_revision':2},
            'manifest_hash':hashlib.sha256(raw).hexdigest(),'manifest_size_bytes':len(raw),
            'download_url':'https://invalid.example/not-used'})
    with AxiomClient('http://workspace.test',transport=httpx.MockTransport(respond)) as api:
        api.preview_scene_index(collection_id='collection_1',collection_revision=1,name='Example',note='Test')
        assert 'Idempotency-Key' not in requests[-1].headers
        api.create_scene_index(collection_id='collection_1',collection_revision=1,confirm_local_analysis=True,idempotency_key='index')
        assert requests[-1].headers['Idempotency-Key']=='index'
        api.review_scene_tags('scene_1',expected_review_revision=0,idempotency_key='review')
        assert requests[-1].headers['Idempotency-Key']=='review'
        assert api.export_scene_search('scene_1',tmp_path/'search.json').read_bytes()==raw
        assert all(r.url.host=='workspace.test' for r in requests)
        assert json.loads(requests[-1].url.params['query'])=={'review_revision':2}


def test_sdk_validation_does_not_start_unapproved_work():
    with AxiomClient(transport=httpx.MockTransport(lambda _:pytest.fail('Unexpected request'))) as api:
        with pytest.raises(ValueError):api.scene_index('../outside')
        with pytest.raises(ValueError):api.review_scene_tags('scene_1',expected_review_revision=True,idempotency_key='x')
        with pytest.raises(ValueError):api.create_scene_index(collection_id='collection_1',collection_revision=1,idempotency_key='x')
