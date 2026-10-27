"""Public object-map transport and integrity contracts; no native simulator run."""
import hashlib
import json

import httpx
import pytest

from axiom_sdk import AxiomClient


RIGHTS = {'license': 'Authored SDK test data', 'redistribution': 'permitted'}
DEFINITION = {'schema_version': 'object-map/1', 'name': 'SDK authored map', 'rights': RIGHTS,
              'roads': [{'id': 'road_a', 'geometries': [{'kind': 'line', 'length_m': 80}],
                         'lanes': [{'id': -1, 'width_m': 3.5}]}]}


def test_object_map_public_methods_use_exact_project_routes_and_pins():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'accepted': True})
    with AxiomClient('http://workspace.test', token='project-engineer',
                     transport=httpx.MockTransport(respond)) as client:
        client.object_behaviors()
        client.object_map_capabilities(project_id='project_a')
        client.object_maps(project_id='project_a', limit=7, offset=14)
        client.object_map('source_a', project_id='project_a', revision=3)
        client.object_map('source_a', project_id='project_a')
        client.preview_object_map(DEFINITION, project_id='project_a')
        client.save_object_map('source_a', DEFINITION, expected_revision=2, project_id='project_a')
        client.build_object_map('source_a', revision=3, sha256='a'*64, server_version='1ae5356',
                                expected_map_revision=4, project_id='project_a')
        client.import_object_map('<OpenDRIVE>\r\n</OpenDRIVE>\n', RIGHTS,
                                 name='Imported original', project_id='project_a')
    assert [(request.method, request.url.path) for request in requests] == [
        ('GET', '/api/v1/object-behaviors'), ('GET', '/api/v1/object-maps/capabilities'),
        ('GET', '/api/v1/object-maps'), ('GET', '/api/v1/object-maps/source_a'),
        ('GET', '/api/v1/object-maps/source_a'), ('POST', '/api/v1/object-maps/preview'),
        ('POST', '/api/v1/object-maps'), ('POST', '/api/v1/object-maps/source_a/build'),
        ('POST', '/api/v1/object-maps/import')]
    assert all(request.headers['Authorization'] == 'Bearer project-engineer' for request in requests)
    assert dict(requests[1].url.params) == {'project_id': 'project_a'}
    assert dict(requests[2].url.params) == {'project_id': 'project_a', 'limit': '7', 'offset': '14'}
    assert dict(requests[3].url.params) == {'project_id': 'project_a', 'revision': '3'}
    assert dict(requests[4].url.params) == {'project_id': 'project_a'}
    assert json.loads(requests[5].content) == {'definition': DEFINITION, 'project_id': 'project_a'}
    assert json.loads(requests[6].content) == {'id': 'source_a', 'definition': DEFINITION,
        'project_id': 'project_a', 'expected_revision': 2}
    assert json.loads(requests[7].content) == {'project_id': 'project_a', 'revision': 3,
        'sha256': 'a'*64, 'server_version': '1ae5356', 'expected_map_revision': 4}
    assert json.loads(requests[8].content) == {'content': '<OpenDRIVE>\r\n</OpenDRIVE>\n',
        'rights': RIGHTS, 'name': 'Imported original', 'project_id': 'project_a'}


@pytest.mark.parametrize('format', ['opendrive', 'source_opendrive', 'json'])
def test_export_checks_original_utf8_bytes_and_preserves_text(format):
    raw = ('{"name":"도로","value":1}\n' if format == 'json'
           else '<OpenDRIVE>\r\n<!-- 도로 원본 -->\r\n</OpenDRIVE>\n').encode('utf-8')
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=raw, headers={
            'X-Content-SHA256': hashlib.sha256(raw).hexdigest(),
            # Decoding uses the API's documented UTF-8 contract after byte hashing.
            'Content-Type': 'text/plain; charset=iso-8859-1'})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        assert client.export_object_map('source_a', project_id='project_a', revision=2,
                                        format=format) == raw.decode('utf-8')
    assert len(requests) == 1 and requests[0].method == 'GET'
    assert requests[0].url.path == '/api/v1/object-maps/source_a/export'
    assert dict(requests[0].url.params) == {'project_id': 'project_a', 'revision': '2', 'format': format}


@pytest.mark.parametrize('header', [None, 'bad', 'A'*64, '0'*64])
def test_export_rejects_missing_malformed_and_mismatched_digest(header):
    def respond(_):
        return httpx.Response(200, content=b'original', headers={} if header is None else {'X-Content-SHA256': header})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match='hash mismatch'):
            client.export_object_map('source_a')


def test_export_rejects_hash_of_normalized_instead_of_original_bytes():
    raw = b'<OpenDRIVE>\r\n</OpenDRIVE>\r\n'
    normalized_hash = hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()
    with AxiomClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=raw, headers={'X-Content-SHA256': normalized_hash}))) as client:
        with pytest.raises(ValueError, match='hash mismatch'):
            client.export_object_map('source_a', format='source_opendrive')


@pytest.mark.parametrize('method,args,kwargs', [
    ('object_map_capabilities', (), {'project_id': '../other'}),
    ('object_maps', (), {'limit': 0}), ('object_maps', (), {'limit': 1001}),
    ('object_maps', (), {'limit': True}), ('object_maps', (), {'offset': -1}),
    ('object_maps', (), {'offset': False}), ('object_maps', (), {'project_id': 'a'*65}),
    ('object_map', ('../other',), {}), ('object_map', ('source_a',), {'revision': 0}),
    ('object_map', ('source_a',), {'revision': True}),
    ('export_object_map', ('source_a',), {'revision': -1}),
    ('export_object_map', ('source_a',), {'format': 'yaml'}),
    ('export_object_map', ('source_a',), {'format': []}),
    ('save_object_map', ('source_a', DEFINITION), {'expected_revision': -1}),
    ('save_object_map', ('source_a', DEFINITION), {'expected_revision': False}),
    ('save_object_map', ('source_a', []), {}),
    ('preview_object_map', ({'roads': [{}]*129},), {}),
    ('preview_object_map', ({'connections': [{}]*2049},), {}),
    ('preview_object_map', ({'signals': [{}]*513},), {}),
    ('preview_object_map', ({'roads': [{'lanes': []}]},), {}),
    ('preview_object_map', ({'roads': [{'lanes': [{}]*13}]},), {}),
    ('preview_object_map', ({'roads': [{'geometries': [{}]*33}]},), {}),
    ('preview_object_map', ({'roads': 'not a list'},), {}),
    ('build_object_map', ('source_a',), {'revision': 0, 'sha256': 'a'*64, 'server_version': 'carla'}),
    ('build_object_map', ('source_a',), {'revision': True, 'sha256': 'a'*64, 'server_version': 'carla'}),
    ('build_object_map', ('source_a',), {'revision': 1, 'sha256': 'not-a-hash', 'server_version': 'carla'}),
    ('build_object_map', ('source_a',), {'revision': 1, 'sha256': 'a'*64, 'server_version': ''}),
    ('build_object_map', ('source_a',), {'revision': 1, 'sha256': 'a'*64, 'server_version': 'v'*201}),
    ('build_object_map', ('source_a',), {'revision': 1, 'sha256': 'a'*64, 'server_version': 'carla', 'expected_map_revision': True}),
    ('import_object_map', ('', RIGHTS), {}),
    ('import_object_map', ('<OpenDRIVE/>', []), {}),
    ('import_object_map', ('<OpenDRIVE/>', RIGHTS), {'name': ' '}),
])
def test_invalid_map_contract_fails_before_network(method, args, kwargs):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected HTTP request'))) as client:
        with pytest.raises(ValueError):
            getattr(client, method)(*args, **kwargs)
