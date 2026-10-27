"""Recovery never retries simulation, preserves exact intent and verifies downloads."""
import hashlib
import json

import httpx
import pytest

from axiom_sdk import AxiomClient, AxiomError


def test_explicit_retrieval_retry_preserves_identical_body_and_key():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadError('Accepted retrieval reply lost')
        return httpx.Response(202, json={'id': 'recovery_a', 'status': 'queued'})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError):
            client.submit_execution_recovery('job_a', note='Retrieve original output', idempotency_key='intent')
        assert len(calls) == 1
        client.submit_execution_recovery('job_a', note='Retrieve original output', idempotency_key='intent')
    assert calls[0].content == calls[1].content
    assert json.loads(calls[0].content) == {'source_job_id': 'job_a', 'note': 'Retrieve original output'}
    assert all(r.url.path == '/api/v1/execution-recoveries' and r.headers['Idempotency-Key'] == 'intent' for r in calls)


@pytest.mark.parametrize('kwargs', [dict(note=''), dict(note=' '*10), dict(note='x'*1001),
                                   dict(idempotency_key=None), dict(idempotency_key=''), dict(idempotency_key=' '),
                                   dict(idempotency_key='x'*201), dict(source_job_id='../run')])
def test_invalid_requests_fail_without_http(kwargs):
    body = {'source_job_id': 'job_a', 'note': 'Retrieve', 'idempotency_key': 'intent', **kwargs}
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('No request expected'))) as client:
        with pytest.raises(ValueError):
            client.submit_execution_recovery(**body)


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled', 'interrupted'])
def test_terminal_wait_keeps_non_success_outcome_and_never_executes(state):
    calls = []
    value = {'id': 'recovery_a', 'status': state, 'outcome': 'diagnostics_only', 'run_id': 'run_partial'}
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=value)
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        assert client.wait_execution_recovery('recovery_a', timeout=0) == value
    assert len(calls) == 1 and calls[0].method == 'GET'


def test_wait_timeout_does_not_cancel_retrieval_or_original_execution():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'recovery_a', 'status': 'running'})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TimeoutError, match='no cancellation or resimulation'):
            client.wait_execution_recovery('recovery_a', timeout=0)
    assert [r.method for r in calls] == ['GET']


@pytest.mark.parametrize('options', [{'timeout': float('nan')}, {'timeout': -1}, {'timeout': True},
                                    {'poll_interval': 0}, {'poll_interval': float('inf')}])
def test_invalid_wait_limits_fail_before_network(options):
    with AxiomClient(transport=httpx.MockTransport(lambda _: pytest.fail('No request expected'))) as client:
        with pytest.raises(ValueError):
            client.wait_execution_recovery('recovery_a', **options)


@pytest.mark.parametrize('remote_archive', [True, False])
def test_exact_authenticated_download_ignores_remote_metadata_url(tmp_path, remote_archive):
    content = b'{"exact":"source bytes"}'
    sha = hashlib.sha256(content).hexdigest()
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == 'api.test' and request.headers['Authorization'] == 'Bearer secret'
        if request.url.path.endswith('/recovery_a'):
            return httpx.Response(200, json={'id': 'recovery_a', 'status': 'completed', 'archive_hash': sha,
                'archive_size_bytes': len(content), 'manifest_hash': sha, 'manifest_size_bytes': len(content),
                'archive_download_url': 'https://other.test/steal', 'download_url': 'https://other.test/steal'})
        assert request.url.path.endswith('/archive' if remote_archive else '/export')
        return httpx.Response(200, content=content)
    output = tmp_path / 'evidence.json'
    with AxiomClient('https://api.test', token='secret', transport=httpx.MockTransport(handler)) as client:
        method = client.export_remote_execution_archive if remote_archive else client.export_execution_recovery
        assert method('recovery_a', output) == output
    assert output.read_bytes() == content and len(calls) == 2


@pytest.mark.parametrize('corruption', ['digest', 'redirect', 'truncated'])
def test_bad_archive_never_publishes_or_deletes_existing_file(tmp_path, corruption):
    content = b'archive'
    def handler(request):
        if request.url.path.endswith('/recovery_a'):
            return httpx.Response(200, json={'id': 'recovery_a', 'status': 'completed',
                'archive_hash': hashlib.sha256(content).hexdigest(), 'archive_size_bytes': len(content)})
        if corruption == 'redirect':
            return httpx.Response(302, headers={'Location': 'https://elsewhere.test'})
        return httpx.Response(200, content=b'x'*len(content) if corruption == 'digest' else b'arc')
    path = tmp_path / 'archive.json'
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises((ValueError, AxiomError)):
            client.export_remote_execution_archive('recovery_a', path)
        assert not path.exists()
        path.write_bytes(b'prior user bytes')
        with pytest.raises((FileExistsError, ValueError, AxiomError)):
            client.export_remote_execution_archive('recovery_a', path)
        assert path.read_bytes() == b'prior user bytes'


def test_routes_filters_and_identity_are_not_guessed():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/remote-execution'):
            return httpx.Response(200, json={'source_job_id': 'job_a', 'supported': False})
        if request.url.path.endswith('/cancel'):
            return httpx.Response(200, json={'id': 'recovery_a', 'status': 'cancel_requested'})
        return httpx.Response(200, json={'items': [], 'next_before_seq': None})
    with AxiomClient(transport=httpx.MockTransport(handler)) as client:
        assert not client.inspect_remote_execution('job_a')['supported']
        client.execution_recoveries(source_job_id='job_a', limit=2, before_seq=9)
        assert client.cancel_execution_recovery('recovery_a')['status'] == 'cancel_requested'
    assert dict(calls[1].url.params) == {'source_job_id': 'job_a', 'limit': '2', 'before_seq': '9'}
    assert [r.method for r in calls] == ['GET', 'GET', 'POST']


def test_cli_requires_explicit_recovery_key(monkeypatch):
    from axiom_sdk.cli import main
    monkeypatch.setattr('sys.argv', ['axiom', 'submit-execution-recovery', 'job_a', '--note', 'Retrieve'])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
