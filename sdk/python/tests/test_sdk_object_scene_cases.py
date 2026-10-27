import hashlib
import json

import httpx
import pytest
from axiom_sdk import AxiomClient

SHA = 'a' * 64
LINK = {'project_id': 'default', 'execution_id': 'job-city', 'execution_manifest_hash': SHA,
        'scene_sha256': 'b' * 64, 'frame_index': 3, 'time_s': .2}
CASE = {'id': 'case-city', 'revision': 1, 'object_sim_evidence': LINK}
ARGS = {'execution_manifest_hash': SHA, 'scene_sha256': 'b' * 64, 'frame_index': 3, 'time_s': .2,
        'title': 'Retained frame', 'note': 'Simulation investigation only', 'idempotency_key': 'same-request'}


def test_case_create_and_read_use_exact_project_execution_frame_pins():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(201 if request.method == 'POST' else 200, json=CASE)
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        assert client.create_object_scene_case('job-city', **ARGS) == CASE
        assert client.object_scene_case('job-city', 'case-city', revision=1) == CASE
    assert calls[0].url.path == '/api/v1/object-sim/executions/job-city/cases'
    assert calls[0].headers['idempotency-key'] == 'same-request'
    body = json.loads(calls[0].content)
    assert all(body[key] == LINK[key] for key in ('project_id', 'execution_manifest_hash', 'scene_sha256', 'frame_index', 'time_s'))
    assert dict(calls[1].url.params) == {'project_id': 'default', 'revision': '1'}


def test_case_export_verifies_caller_pin_and_response_bytes():
    raw = json.dumps(CASE).encode(); sha = hashlib.sha256(raw).hexdigest()
    with AxiomClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw, headers={'X-Content-SHA256': sha}))) as client:
        assert client.export_object_scene_case('job-city', 'case-city', expected_manifest_hash=sha) == raw
        with pytest.raises(ValueError, match='SHA-256'):
            client.export_object_scene_case('job-city', 'case-city', expected_manifest_hash=SHA)
    with AxiomClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'changed', headers={'X-Content-SHA256': sha}))) as client:
        with pytest.raises(ValueError, match='SHA-256'):
            client.export_object_scene_case('job-city', 'case-city', expected_manifest_hash=sha)


def test_case_rejects_invalid_or_substituted_selection():
    with AxiomClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={**CASE, 'object_sim_evidence': {**LINK, 'frame_index': 4}}))) as client:
        with pytest.raises(ValueError, match='frame'):
            client.create_object_scene_case('job-city', **ARGS)
        for change in ({'time_s': float('nan')}, {'frame_index': True}, {'scene_sha256': 'bad'}):
            with pytest.raises(ValueError): client.create_object_scene_case('job-city', **(ARGS | change))
        with pytest.raises(ValueError): client.object_scene_case('job-city', 'case-city', revision=0)
        with pytest.raises(ValueError, match='project/execution/revision'):
            client.object_scene_case('job-foreign', 'case-city')
