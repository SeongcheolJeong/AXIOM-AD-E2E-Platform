"""Durable transport/OS crash checks with authored results; no simulator."""
import base64
import hashlib
import json
import os
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import AdapterExecutionError, AdapterUnavailable
from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.schemas import ArtifactPayload, SimulationResult
from ade2e.simulation_context import simulation_input_hash
from axiom_carla import durable_worker, worker
from axiom_carla.remote import RemoteCarlaAdapter
from axiom_carla.remote_durable import decode_archive
from axiom_carla.spool import Store, StoreConflict, StoreUnavailable, canonical, sha
from axiom_carla.version import ADAPTER_VERSION
from axiom_carla.wire import encode_result


@pytest.fixture
def attempt_request(carla_scenario):
    return ExecutionAttemptRequest(execution_id='run_durable_test', adapter_id='carla', adapter_version=ADAPTER_VERSION,
        input_hash=simulation_input_hash(carla_scenario, 'carla', ADAPTER_VERSION), scenario=carla_scenario)


@pytest.fixture
def result(worker_payload):
    payload = worker_payload
    artifact = payload['artifact_payloads'][0]
    artifact['content'] = base64.b64decode(artifact.pop('content_base64'))
    return SimulationResult.model_validate(payload)


@pytest.fixture
def durable(tmp_path, monkeypatch):
    monkeypatch.setenv('ADE2E_CARLA_EXECUTION_STORE', str(tmp_path / 'spool'))
    monkeypatch.delenv('ADE2E_CARLA_WORKER_TOKEN', raising=False)
    return Store(tmp_path / 'spool')


def proxy(client, *, mutate=None, calls=None):
    def handle(request):
        if calls is not None:
            calls.append(request.url.path)
        response = client.request(request.method, request.url.raw_path.decode(), content=request.content, headers=dict(request.headers))
        return mutate(request, response) if mutate else response
    return httpx.MockTransport(handle)


def test_response_lost_retrieves_exact_result_without_second_simulation(durable, attempt_request, result, monkeypatch):
    executions = []
    monkeypatch.setattr(durable_worker.CarlaRunner, 'execute', lambda self, value: executions.append(value) or result)
    with TestClient(worker.app) as client:
        def lose(request, response):
            if request.url.path == '/v3/simulate':
                raise httpx.ReadError('authored response loss', request=request)
            return response
        adapter = RemoteCarlaAdapter('http://worker.test', transport=proxy(client, mutate=lose))
        status = adapter.inspect_execution(attempt_request)
        with pytest.raises(AdapterExecutionError, match='uncertain'):
            adapter.execute_attempt(attempt_request, expected_store_id=status.store_id)
        adapter.transport = proxy(client)
        complete = adapter.inspect_execution(attempt_request)
        assert complete.state == 'completed'
        recovered = adapter.retrieve_execution(attempt_request, expected_store_id=status.store_id,
            expected_archive_sha256=complete.archive_sha256)
        assert recovered.result == result
        assert recovered.archive_bytes == durable.find(attempt_request.execution_id).archive()
        with pytest.raises(AdapterExecutionError, match='already exists'):
            adapter.execute_attempt(attempt_request, expected_store_id=status.store_id)
    assert len(executions) == 1


def test_store_change_is_refused_before_native_execute(durable, attempt_request, monkeypatch):
    monkeypatch.setattr(durable_worker.CarlaRunner, 'execute', lambda *_: pytest.fail('native mutation'))
    with TestClient(worker.app) as client:
        adapter = RemoteCarlaAdapter('http://worker.test', transport=proxy(client))
        with pytest.raises(AdapterUnavailable, match='store changed'):
            adapter.execute_attempt(attempt_request, expected_store_id='wrong-store')
        assert client.post('/v3/simulate?store_id=wrong', json=attempt_request.model_dump(mode='json')).status_code == 409
    assert durable.find(attempt_request.execution_id) is None


def test_durable_worker_disallows_uncorrelated_routes(durable, attempt_request, monkeypatch):
    monkeypatch.setattr(durable_worker.CarlaRunner, 'execute', lambda *_: pytest.fail('uncorrelated mutation'))
    with TestClient(worker.app) as client:
        assert client.post('/v1/simulate', json=attempt_request.scenario.model_dump(mode='json')).status_code == 422


def test_same_identity_changed_request_cannot_reexecute(durable, attempt_request, result):
    with durable.begin(attempt_request) as attempt:
        attempt.seal('completed', encode_result(result), cleanup_status='confirmed', execution_ms=1)
    changed = attempt_request.scenario.model_copy(update={'seed': 18})
    new = attempt_request.model_copy(update={'scenario': changed, 'input_hash': simulation_input_hash(changed, 'carla', ADAPTER_VERSION)})
    with pytest.raises(StoreConflict, match='another immutable'):
        with durable.begin(new):
            pytest.fail('reexecuted')


def test_active_owner_is_never_reclassified_as_dead(durable, attempt_request):
    with durable.begin(attempt_request) as attempt:
        attempt.event('world_mutation_intent', {'world_id': '13687974690369489725'})
        assert durable.inspect(attempt_request).state == 'running'
        assert attempt.journal()['entries'][0]['data']['world_id'] == '13687974690369489725'


def test_hard_process_exit_preserves_only_committed_partial(durable, attempt_request, tmp_path):
    request_path = tmp_path / 'request.json'
    request_path.write_bytes(canonical(attempt_request.model_dump(mode='json')))
    program = '''
import os, sys
from pathlib import Path
from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.schemas import ArtifactPayload
from axiom_carla.spool import Store
request=ExecutionAttemptRequest.model_validate_json(Path(sys.argv[2]).read_bytes())
with Store(sys.argv[1]).begin(request) as attempt:
    attempt.event('world_mutation_intent', {'world_id':'13687974690369489725'})
    attempt.event('ego_send_intent', {'acknowledged':False}, content=b'raw-source')
    attempt.checkpoint([ArtifactPayload(name='raw.bin', kind='execution_diagnostic', media_type='application/octet-stream', content=b'committed')], 'before_send')
    os._exit(73)
'''
    process = subprocess.run([sys.executable, '-c', program, str(durable.root), str(request_path)], capture_output=True)
    assert process.returncode == 73, process.stderr.decode()
    status = Store(durable.root).inspect(attempt_request)
    assert status.state == 'interrupted' and status.archive_kind == 'partial'
    assert status.cleanup_status == 'unknown' and status.mutations_started
    attempt = durable.find(attempt_request.execution_id)
    archive = decode_archive(attempt_request, status, attempt.archive())
    assert archive.result is None and archive.failure.artifact_payloads[0].content == b'committed'
    assert attempt.journal(include_blobs=True)['entries'][1]['blob_base64'] == base64.b64encode(b'raw-source').decode()
    changed = attempt_request.model_copy(update={'execution_id': 'run_second'})
    with pytest.raises(StoreConflict, match='unresolved engine cleanup'):
        with durable.begin(changed):
            pytest.fail('mutated quarantined engine')
    assert Store(durable.root).inspect(attempt_request).archive_sha256 == status.archive_sha256


@pytest.mark.parametrize('field', ['execution_id', 'input_hash', 'request_hash', 'store_id', 'worker_adapter_version', 'outcome'])
def test_archive_identity_tamper_is_refused_even_when_outer_digest_is_rehashed(durable, attempt_request, result, field):
    with durable.begin(attempt_request) as attempt:
        attempt.seal('completed', encode_result(result), cleanup_status='confirmed', execution_ms=1)
    status = durable.inspect(attempt_request)
    data = json.loads(attempt.archive()); data[field] = 'altered'
    content = canonical(data)
    status = status.model_copy(update={'archive_sha256': sha(content), 'archive_size_bytes': len(content)})
    with pytest.raises(ValueError, match='correlation'):
        decode_archive(attempt_request, status, content)


def test_journal_tamper_is_not_exported(durable, attempt_request):
    with durable.begin(attempt_request) as attempt:
        attempt.event('raw_source', {}, content=b'original')
        with attempt.db() as db:
            db.execute('UPDATE journal SET blob=?', (b'changed',))
        with pytest.raises(StoreUnavailable, match='integrity'):
            attempt.journal()


def test_authorization_precedes_durable_inspection_or_creation(durable, attempt_request, monkeypatch):
    monkeypatch.setenv('ADE2E_CARLA_WORKER_TOKEN', 'private-test-token')
    with TestClient(worker.app) as client:
        assert client.post('/v3/inspect', json=attempt_request.model_dump(mode='json')).status_code == 401
        assert client.post('/v3/simulate?store_id=' + durable.id, json=attempt_request.model_dump(mode='json')).status_code == 401
    assert durable.find(attempt_request.execution_id) is None


def test_reservation_publication_failure_never_leaves_truncated_owner(durable, attempt_request, monkeypatch):
    import axiom_carla.spool as spool
    def fail_link(*_):
        raise OSError('Injected atomic reservation publication failure')
    monkeypatch.setattr(spool.os, 'link', fail_link)
    with pytest.raises(OSError, match='publication failure'):
        with durable.begin(attempt_request):
            pytest.fail('Native execution entered before ownership publication')
    assert not (durable.root/'quarantine.json').exists()
    assert not list(durable.root.glob('.reservation-*'))
    status = durable.inspect(attempt_request)
    assert status.state == 'interrupted' and status.mutations_started is False and status.cleanup_status == 'not_started'


def test_stopped_pre_mutation_owner_can_reconcile_without_connecting_to_engine(durable, attempt_request, monkeypatch):
    from axiom_carla.reconcile import reconcile
    with durable.begin(attempt_request):
        pass  # abandoned owner before any native intent
    status = durable.inspect(attempt_request)
    assert status.state == 'interrupted'
    monkeypatch.setattr(durable_worker.CarlaRunner, '_connect', lambda *_: pytest.fail('No engine connection for no-mutation proof'))
    plan = reconcile(durable.root, attempt_request.execution_id, expected_store_id=durable.id)
    result = reconcile(durable.root, attempt_request.execution_id, expected_store_id=durable.id,
        expected_journal_sha256=plan['journal_sha256'], cleanup=True)
    assert result['status'] == 'verified_no_mutation' and result['operations'] == []
    assert not (durable.root/'quarantine.json').exists()
    assert durable.inspect(attempt_request).archive_sha256 == status.archive_sha256
