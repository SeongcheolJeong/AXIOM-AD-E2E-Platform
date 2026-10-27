"""Byte preservation and immutable-source transport, without a native engine."""
import base64
import hashlib
import json

import httpx
import pytest

from axiom_sdk import AxiomClient


def test_original_binary_and_upstream_attachment_are_not_text_normalized():
    seen = []
    original = b'PK\x03\x04\xff\x00\r\n'
    upstream = b'<osm>\r\n</osm>\n'
    def respond(request):
        seen.append(request)
        return httpx.Response(201, json={'revision': 1})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.create_map_source(name='지도', filename='roads.zip', source_kind='ngii_shp',
            content=original, rights={'license': 'test'}, source_id='source-a', project_id='pilot',
            attachments=[{'filename': 'source.osm', 'role': 'upstream', 'content': upstream}])
    body = json.loads(seen[0].content)
    assert seen[0].url.path == '/api/v1/map-sources'
    assert body['project_id'] == 'pilot'
    assert base64.b64decode(body['content_base64']) == original
    assert base64.b64decode(body['attachments'][0]['content_base64']) == upstream


def test_qualification_and_publication_keep_source_conversion_and_execution_pins():
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={})
    conversion = {'id': 'conversion-a', 'revision': 1, 'sha256': 'b' * 64}
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.qualify_map_source('source-a', revision=2, sha256='a' * 64,
            conversion_ref=conversion, idempotency_key='map-native-a', project_id='pilot')
        client.publish_map_source('source-a', revision=2, sha256='a' * 64,
            conversion_ref=conversion, execution_id='execution-a', project_id='pilot')
    assert seen[0].headers['Idempotency-Key'] == 'map-native-a'
    for request in seen:
        body = json.loads(request.content)
        assert body['revision'] == 2 and body['sha256'] == 'a' * 64
        assert body['conversion_ref'] == conversion and body['project_id'] == 'pilot'
    assert json.loads(seen[1].content)['execution_id'] == 'execution-a'


@pytest.mark.parametrize('digest', [None, '0' * 64])
def test_original_export_rejects_missing_or_wrong_hash(digest):
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
            content=b'original\r\n', headers={} if digest is None else {'X-Content-SHA256': digest}))) as client:
        with pytest.raises(ValueError, match='hash mismatch'):
            client.export_map_source('source-a')


def test_original_export_preserves_binary_bytes():
    original = b'PK\xff\r\n\x00'
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
            content=original, headers={'X-Content-SHA256': hashlib.sha256(original).hexdigest()}))) as client:
        assert client.export_map_source('source-a', revision=2) == original


def test_invalid_reference_cannot_submit_job():
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected network'))) as client:
        with pytest.raises(ValueError):
            client.qualify_map_source('../escape', revision=1, sha256='a' * 64,
                conversion_ref={'id': 'c', 'revision': 1, 'sha256': 'b' * 64}, idempotency_key='x')
        with pytest.raises(ValueError):
            client.qualify_map_source('source-a', revision=1, sha256='a' * 64,
                conversion_ref={'id': 'c', 'revision': 1, 'sha256': 'bad'}, idempotency_key='x')
