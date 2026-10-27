"""SDK-only HIL batch transport and lost-response checks; no native execution."""
import copy
import json

import httpx
import pytest

from axiom_sdk import AxiomClient, AxiomError


REFS = [dict(id='hil_test_' + str(index), revision=index + 1, sha256=letter * 64)
        for index, letter in enumerate('abc')]
SAVE = dict(name='Three saved conditions', note='Sequential software DUT study',
            tests=REFS, project_id='project_one', idempotency_key='batch-create-stable')


def test_batch_creation_is_one_explicit_project_scoped_request_without_execution_fanout():
    requests = []
    expected = copy.deepcopy(REFS)
    def respond(request):
        requests.append(request)
        return httpx.Response(201, json={'id': 'hil_batch_one', 'revision': 1, 'status': 'draft'})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        result = client.create_hil_sim_batch(**SAVE)
    assert result['status'] == 'draft'
    assert len(requests) == 1
    assert requests[0].url.path == '/api/v1/hil-sim/batches'
    assert requests[0].method == 'POST'
    assert requests[0].headers['Idempotency-Key'] == SAVE['idempotency_key']
    assert json.loads(requests[0].content) == {key: value for key, value in SAVE.items() if key != 'idempotency_key'}
    assert REFS == expected


@pytest.mark.parametrize('tests', [None, True, {}, tuple(REFS), [], REFS[:2], REFS * 2,
    [REFS[0], REFS[1], REFS[0]],
    [{**REFS[0], 'revision': True}, *REFS[1:]],
    [{**REFS[0], 'revision': 0}, *REFS[1:]],
    [{**REFS[0], 'revision': 1.0}, *REFS[1:]],
    [{**REFS[0], 'sha256': 'not-a-hash'}, *REFS[1:]],
    [{**REFS[0], 'id': '../test'}, *REFS[1:]],
    [{**REFS[0], 'project_id': 'another-project'}, *REFS[1:]],
    [{**REFS[0], 'experiment': {}}, *REFS[1:]]])
def test_bad_or_duplicate_selection_fails_before_transport(tests):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected network'))) as client:
        with pytest.raises(ValueError):
            client.create_hil_sim_batch(**{**SAVE, 'tests': tests})


@pytest.mark.parametrize('change', [
    {'project_id': '../project'}, {'project_id': True}, {'project_id': ''},
    {'name': ' '}, {'name': 5}, {'note': None},
    {'idempotency_key': ''}, {'idempotency_key': ' '}, {'idempotency_key': 'x' * 181},
])
def test_invalid_creation_types_scope_and_key_never_reach_transport(change):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected network'))) as client:
        with pytest.raises(ValueError):
            client.create_hil_sim_batch(**{**SAVE, **change})


def test_reads_cancel_and_export_keep_project_scope_and_expected_revision():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.hil_sim_batches(project_id='project_one', limit=5, offset=2)
        client.hil_sim_batch('hil_batch_one', project_id='project_one')
        client.cancel_hil_sim_batch('hil_batch_one', project_id='project_one',
                                   expected_revision=4, idempotency_key='cancel-stable')
        client.export_hil_sim_batch('hil_batch_one', project_id='project_one')
    assert [(request.method, request.url.path) for request in requests] == [
        ('GET', '/api/v1/hil-sim/batches'), ('GET', '/api/v1/hil-sim/batches/hil_batch_one'),
        ('POST', '/api/v1/hil-sim/batches/hil_batch_one/cancel'),
        ('GET', '/api/v1/hil-sim/batches/hil_batch_one/export')]
    assert requests[0].url.params['limit'] == '5' and requests[0].url.params['offset'] == '2'
    assert all(request.url.params['project_id'] == 'project_one' for request in requests if request.method == 'GET')
    assert json.loads(requests[2].content) == dict(project_id='project_one', expected_revision=4)
    assert requests[2].headers['Idempotency-Key'] == 'cancel-stable'


@pytest.mark.parametrize('revision', [0, -1, True, 1.0, '1', None])
def test_action_revision_is_strict_before_transport(revision):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected network'))) as client:
        with pytest.raises(ValueError):
            client.cancel_hil_sim_batch('hil_batch_one', expected_revision=revision, idempotency_key='cancel')


@pytest.mark.parametrize('status', [403, 404, 409, 422, 503])
def test_server_rejection_does_not_change_project_or_retry(status):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={'detail': 'Pinned batch request rejected'})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AxiomError) as caught:
            client.create_hil_sim_batch(**SAVE)
    assert caught.value.status_code == status and len(requests) == 1
    assert json.loads(requests[0].content)['project_id'] == 'project_one'


def test_lost_create_response_requires_explicit_retry_with_same_key_and_body():
    requests = []
    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout('Response lost after durable acceptance', request=request)
        return httpx.Response(201, json={'id': 'same_durable_batch', 'revision': 1})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.create_hil_sim_batch(**SAVE)
        assert len(requests) == 1
        assert client.create_hil_sim_batch(**SAVE)['id'] == 'same_durable_batch'
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert requests[0].headers['Idempotency-Key'] == requests[1].headers['Idempotency-Key'] == 'batch-create-stable'


def test_preview_start_and_resume_bind_reviewed_revision_hash_without_client_fanout():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'revision': 4, 'preview_hash': 'd' * 64})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.preview_hil_sim_batch('hil_batch_one', project_id='project_one', expected_revision=3)
        client.start_hil_sim_batch('hil_batch_one', project_id='project_one', expected_revision=3,
            preview_hash='d' * 64, idempotency_key='start-stable')
        client.resume_hil_sim_batch('hil_batch_one', project_id='project_one', expected_revision=8,
            preview_hash='e' * 64, idempotency_key='resume-stable')
    assert [request.url.path for request in requests] == [
        '/api/v1/hil-sim/batches/hil_batch_one/preview',
        '/api/v1/hil-sim/batches/hil_batch_one/start', '/api/v1/hil-sim/batches/hil_batch_one/resume']
    assert all(request.method == 'POST' for request in requests)
    assert json.loads(requests[0].content) == dict(project_id='project_one', expected_revision=3)
    assert 'Idempotency-Key' not in requests[0].headers
    assert json.loads(requests[1].content) == dict(project_id='project_one', expected_revision=3, preview_hash='d' * 64)
    assert json.loads(requests[2].content) == dict(project_id='project_one', expected_revision=8, preview_hash='e' * 64)
    assert requests[1].headers['Idempotency-Key'] == 'start-stable'
    assert requests[2].headers['Idempotency-Key'] == 'resume-stable'


@pytest.mark.parametrize('action', ['start_hil_sim_batch', 'resume_hil_sim_batch'])
@pytest.mark.parametrize('change', [dict(preview_hash=None), dict(preview_hash=True), dict(preview_hash='bad'),
    dict(preview_hash='A' * 64), dict(expected_revision=True), dict(project_id='../foreign'), dict(idempotency_key='')])
def test_invalid_reviewed_action_never_reaches_transport(action, change):
    values = dict(expected_revision=3, preview_hash='a' * 64, idempotency_key='explicit-intent', project_id='project_one')
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('Unexpected network'))) as client:
        with pytest.raises(ValueError):
            getattr(client, action)('hil_batch_one', **{**values, **change})


@pytest.mark.parametrize('action', ['start_hil_sim_batch', 'resume_hil_sim_batch'])
def test_lost_action_response_keeps_exact_intent_for_explicit_retry(action):
    requests = []
    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout('Accepted action response lost', request=request)
        return httpx.Response(200, json={'id': 'hil_batch_one', 'revision': 4, 'status': 'running'})
    values = dict(expected_revision=3, preview_hash='a' * 64, idempotency_key='action-stable', project_id='project_one')
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.ReadTimeout):
            getattr(client, action)('hil_batch_one', **values)
        assert len(requests) == 1
        assert getattr(client, action)('hil_batch_one', **values)['status'] == 'running'
    assert len(requests) == 2 and requests[0].content == requests[1].content
    assert requests[0].headers['Idempotency-Key'] == requests[1].headers['Idempotency-Key'] == 'action-stable'


def test_five_exact_refs_allow_same_test_at_different_revisions_in_order():
    selected = [*REFS, {**REFS[0], 'revision': 4, 'sha256': 'd' * 64},
        {**REFS[0], 'revision': 5, 'sha256': 'e' * 64}]
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(201, json={'ok': True})
    with AxiomClient(transport=httpx.MockTransport(respond)) as client:
        client.create_hil_sim_batch(**{**SAVE, 'tests': selected})
    assert json.loads(requests[0].content)['tests'] == selected
