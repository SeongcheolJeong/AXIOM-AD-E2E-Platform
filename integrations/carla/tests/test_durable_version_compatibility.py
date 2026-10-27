"""0.8 read compatibility over stored 0.5/0.6/0.7 bytes; authored worlds, no engine."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from ade2e.adapters import AdapterExecutionError, AdapterInputError, AdapterUnavailable
from ade2e.execution_attempt import ExecutionAttemptRequest
from ade2e.schemas import ArtifactPayload, SimulationResult
from ade2e.simulation_context import simulation_input_hash
from axiom_carla import durable_worker, remote_durable, stack_bridge, version, wire, worker
from axiom_carla.remote import RemoteCarlaAdapter
from axiom_carla.runner import CarlaRunner
from axiom_carla.spool import Store, StoreConflict, StoreUnavailable, canonical, sha
from test_durable_execution import proxy
from test_live_stack import install_live_world, live_request


def request_for(scenario, *, context=False, adapter_version='0.5.0'):
    value = live_request(scenario) if context else None
    if value:
        scenario = value.scenario
    context = value.context if value else None
    return ExecutionAttemptRequest(execution_id='run_historical_050', adapter_id='carla', adapter_version=adapter_version,
        scenario=scenario, context=context, input_hash=simulation_input_hash(scenario, 'carla', adapter_version, context))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('ADE2E_CARLA_EXECUTION_STORE', str(tmp_path / 'store'))
    monkeypatch.delenv('ADE2E_CARLA_WORKER_TOKEN', raising=False)
    return Store(tmp_path / 'store')


def freeze_failure(attempt, request, artifacts, *, phase='before_send', old_wire=False):
    context = request.simulation_request() if request.context else None
    payload = wire.encode_failure('Execution interrupted; retained committed checkpoint at ' + phase,
                                 artifacts, request=context, adapter_version=request.adapter_version)
    if old_wire:
        raw = canonical(payload)
        with attempt.db() as db:
            meta = attempt.meta()
            db.execute('INSERT INTO checkpoint VALUES(1,?,?,?)', (raw, sha(raw), phase))
            meta.update(checkpoint_phase=phase, checkpoint_journal_count=meta['journal_count'])
            attempt._write_meta(db, meta)
    else:
        attempt.checkpoint(artifacts, phase)
    return payload


@pytest.mark.parametrize('context', [False, True])
@pytest.mark.parametrize('historical_version', ['0.5.0', '0.6.0', '0.7.0'])
def test_old_completed_archive_reads_exactly_after_upgrade_without_any_engine_call(store, carla_scenario, worker_payload, monkeypatch, context, historical_version):
    request = request_for(carla_scenario, context=context, adapter_version=historical_version)
    if context:
        # Produce a real shared-kernel/MCAP/physics chain over the authored fake
        # world under the historical version. No simulator library is involved.
        with monkeypatch.context() as old:
            old.setattr(version, 'ADAPTER_VERSION', historical_version)
            old.setattr(stack_bridge, 'ADAPTER_VERSION', historical_version)
            install_live_world(old)
            result = CarlaRunner().execute_with_context(request.simulation_request())
    else:
        value = worker_payload
        value['artifact_payloads'] = [ArtifactPayload(name='source.bin', kind='diagnostic', media_type='application/octet-stream', content=b'original050')]
        result = SimulationResult.model_validate(value)
    payload = wire.encode_result(result, request=request.simulation_request() if context else None, adapter_version=historical_version)
    with store.begin(request) as attempt:
        attempt.event('source_observed', {'original_version': historical_version}, content=b'original raw')
        attempt.seal('completed', payload, execution_ms=123.0, cleanup_status='confirmed')
    original = attempt.archive()
    original_meta = attempt.meta()
    journal = attempt.journal(include_blobs=True)
    monkeypatch.setattr(durable_worker.CarlaRunner, 'execute', lambda *_: pytest.fail('Historical lookup executed engine'))
    monkeypatch.setattr(durable_worker.CarlaRunner, 'execute_with_context', lambda *_: pytest.fail('Historical lookup executed stack'))
    with TestClient(worker.app) as client:
        calls = []
        adapter = RemoteCarlaAdapter('http://worker.test', transport=proxy(client, calls=calls))
        assert adapter.metadata().version == '0.8.0'
        assert adapter.remote_recovery_versions() == ('0.5.0', '0.6.0', '0.7.0', '0.8.0')
        status = adapter.inspect_execution(request)
        recovered = adapter.retrieve_execution(request, expected_store_id=store.id, expected_archive_sha256=status.archive_sha256)
        assert recovered.archive_bytes == original and recovered.result == result
        assert status.adapter_version == historical_version and status.state == 'completed'
        assert '/health' not in calls and not any(path.endswith('/simulate') for path in calls)
    assert attempt.meta() == original_meta
    assert attempt.archive() == original and attempt.journal(include_blobs=True) == journal


@pytest.mark.parametrize('context', [False, True])
@pytest.mark.parametrize('format', ['full_wire', 'references', 'no_checkpoint'])
@pytest.mark.parametrize('historical_version', ['0.5.0', '0.6.0', '0.7.0'])
def test_old_partial_recovery_retains_original_inner_outer_version_and_bytes(store, carla_scenario, monkeypatch, context, format, historical_version):
    request = request_for(carla_scenario, context=context, adapter_version=historical_version)
    artifact = ArtifactPayload(name='source.bin', kind='execution_diagnostic', media_type='application/octet-stream', content=b'\x00\xffoldsource')
    with store.begin(request) as attempt:
        attempt.event('source_observed', {'sequence': 7}, content=b'raw050')
        if format == 'no_checkpoint':
            expected = wire.encode_failure('Worker owner stopped before a complete checkpoint. Native journal may retain earlier intent; cleanup is unknown.',
                request=request.simulation_request() if context else None, adapter_version=historical_version)
        else:
            expected = freeze_failure(attempt, request, [artifact], old_wire=format == 'full_wire')
    original_journal = attempt.journal(include_blobs=True)
    monkeypatch.setattr(durable_worker.CarlaRunner, 'execute', lambda *_: pytest.fail('Unexpected engine dispatch'))
    monkeypatch.setattr(durable_worker.CarlaRunner, 'execute_with_context', lambda *_: pytest.fail('Unexpected stack dispatch'))
    with TestClient(worker.app) as client:
        calls = []
        adapter = RemoteCarlaAdapter('http://worker.test', transport=proxy(client, calls=calls))
        status = adapter.inspect_execution(request)
        recovered = adapter.retrieve_execution(request, expected_store_id=store.id, expected_archive_sha256=status.archive_sha256)
        assert recovered.result is None and recovered.failure is not None
        outer = json.loads(recovered.archive_bytes)
        assert outer['worker_adapter_version'] == outer['payload']['worker_adapter_version'] == historical_version
        assert canonical(outer['payload']) == canonical(expected)
        assert outer['request_hash'] == request.request_hash and outer['input_hash'] == request.input_hash
        assert status.state == 'interrupted' and status.adapter_version == historical_version
        assert not any(path.endswith('/simulate') for path in calls)
        original = recovered.archive_bytes
        assert adapter.retrieve_execution(request, expected_store_id=store.id, expected_archive_sha256=status.archive_sha256).archive_bytes == original
    assert attempt.journal(include_blobs=True) == original_journal


def test_historical_native_live_failure_chain_survives_private_checkpoint_materialization(store, carla_scenario, monkeypatch):
    request = request_for(carla_scenario, context=True)
    with monkeypatch.context() as old:
        old.setattr(version, 'ADAPTER_VERSION', '0.5.0')
        old.setattr(stack_bridge, 'ADAPTER_VERSION', '0.5.0')
        install_live_world(old, fault='readback')
        with pytest.raises(AdapterExecutionError) as caught:
            CarlaRunner().execute_with_context(request.simulation_request())
        artifacts = caught.value.artifact_payloads
    with store.begin(request) as attempt:
        expected = freeze_failure(attempt, request, artifacts, phase='next_physics_before_optional_rpc')
    with TestClient(worker.app) as client:
        adapter = RemoteCarlaAdapter('http://worker.test', transport=proxy(client))
        status = adapter.inspect_execution(request)
        recovered = adapter.retrieve_execution(request, expected_store_id=store.id, expected_archive_sha256=status.archive_sha256)
    assert canonical(json.loads(recovered.archive_bytes)['payload']) == canonical(expected)
    assert recovered.failure.artifact_payloads == list(artifacts)


@pytest.mark.parametrize('unsupported', ['0.4.1', '0.5.1', '0.6.1', '1.0.0', 'future'])
def test_unknown_versions_refuse_both_transport_and_worker_before_store_creation(store, carla_scenario, unsupported):
    request = request_for(carla_scenario, adapter_version=unsupported)
    adapter = RemoteCarlaAdapter('http://worker.test', transport=httpx.MockTransport(lambda _: pytest.fail('Unsupported version sent over network')))
    with pytest.raises(AdapterInputError):
        adapter.inspect_execution(request)
    with pytest.raises(AdapterInputError):
        adapter.execute_attempt(request, expected_store_id=store.id)
    with TestClient(worker.app) as client:
        assert client.post('/v3/inspect', json=request.model_dump(mode='json')).status_code == 422
        assert client.post('/v3/simulate', params={'store_id': store.id}, json=request.model_dump(mode='json')).status_code == 422
    assert store.find(request.execution_id) is None


def test_old_version_can_be_inspected_but_cannot_start_new_execution(store, carla_scenario, monkeypatch):
    request = request_for(carla_scenario)
    monkeypatch.setattr(durable_worker.CarlaRunner, 'execute', lambda *_: pytest.fail('Old request dispatched'))
    with TestClient(worker.app) as client:
        calls = []
        adapter = RemoteCarlaAdapter('http://worker.test', transport=proxy(client, calls=calls))
        assert adapter.inspect_execution(request).state == 'not_found'
        with pytest.raises(AdapterInputError):
            adapter.execute_attempt(request, expected_store_id=store.id)
        assert client.post('/v3/simulate', params={'store_id': store.id}, json=request.model_dump(mode='json')).status_code == 422
        assert calls == ['/v3/inspect']
    assert store.find(request.execution_id) is None


def test_original_execution_id_cannot_be_reused_by_relabeling_to_current_version(store, carla_scenario):
    old = request_for(carla_scenario)
    with store.begin(old) as attempt:
        freeze_failure(attempt, old, [])
    before = attempt.meta()
    current = request_for(carla_scenario, adapter_version='0.8.0')
    with TestClient(worker.app) as client:
        assert client.post('/v3/inspect', json=current.model_dump(mode='json')).status_code == 409
        assert client.post('/v3/simulate', params={'store_id': store.id}, json=current.model_dump(mode='json')).status_code == 409
    assert attempt.meta() == before


@pytest.mark.parametrize('damage', ['worker_version', 'request_version', 'request_hash', 'future_consistent'])
def test_corrupt_original_version_is_refused_before_partial_archive_seal(store, carla_scenario, damage):
    request = request_for(carla_scenario)
    with store.begin(request) as attempt:
        freeze_failure(attempt, request, [])
        with attempt.db() as db:
            meta = attempt.meta()
            if damage == 'worker_version':
                meta['worker_adapter_version'] = '0.6.0'
            elif damage == 'request_version':
                meta['request']['adapter_version'] = '0.6.0'
            elif damage == 'request_hash':
                meta['request_hash'] = 'f' * 64
            else:
                future = request_for(carla_scenario, adapter_version='9.0.0')
                from axiom_carla.spool import identity
                meta.update(identity(future), request=future.model_dump(mode='json'))
            attempt._write_meta(db, meta)
    with pytest.raises(StoreUnavailable):
        attempt.recover_abandoned()
    with attempt.db() as db:
        assert db.execute('SELECT count(*) FROM archive').fetchone()[0] == 0


@pytest.mark.parametrize('damage', ['inner_version', 'outer_version'])
def test_archive_version_tamper_rejected_even_with_recomputed_hash(store, carla_scenario, damage):
    request = request_for(carla_scenario)
    with store.begin(request) as attempt:
        payload = wire.encode_failure('original failure', adapter_version='0.5.0')
        attempt.seal('failed', payload)
    content = json.loads(attempt.archive())
    if damage == 'inner_version':
        content['payload']['worker_adapter_version'] = '0.6.0'
    else:
        content['worker_adapter_version'] = '0.6.0'
    raw = canonical(content)
    status = store.inspect(request).model_copy(update={'archive_sha256': sha(raw), 'archive_size_bytes': len(raw)})
    with pytest.raises(ValueError, match='identity|correlation'):
        remote_durable.decode_archive(request, status, raw)


def test_default_wire_decoder_stays_current_and_old_version_requires_explicit_pin():
    payload = wire.encode_failure('old retained failure', adapter_version='0.5.0')
    with pytest.raises(ValueError):
        wire.decode_failure(payload)
    assert wire.decode_failure(payload, adapter_version='0.5.0')[0] == 'old retained failure'
    with pytest.raises(ValueError):
        wire.encode_failure('unknown', adapter_version='future')


def test_new_dense_profile_cannot_be_claimed_as_historical_050_on_any_read_boundary(store, carla_scenario):
    from ade2e.simulation_context import SimulationControlSelection, build_simulation_context
    original = request_for(carla_scenario, context=True)
    prior = original.context
    selection = SimulationControlSelection(stack_id=prior.stack_snapshot.id, stack_revision=prior.stack_snapshot.revision,
        rig_id=prior.rig_snapshot.id, rig_revision=prior.rig_snapshot.revision, lidar_sensor_key=prior.lidar_sensor_key,
        sensor_profile_id='carla-lidar-ego-dense/1')
    dense = build_simulation_context(prior.stack_snapshot, prior.rig_snapshot, selection, original.scenario.seed)
    request = ExecutionAttemptRequest(execution_id=original.execution_id, adapter_id='carla', adapter_version='0.5.0',
        scenario=original.scenario, context=dense, input_hash=simulation_input_hash(original.scenario, 'carla', '0.5.0', dense))
    adapter = RemoteCarlaAdapter('http://worker.test', transport=httpx.MockTransport(lambda _: pytest.fail('Unsupported historical profile crossed network')))
    with pytest.raises(AdapterInputError):
        adapter.inspect_execution(request)
    with pytest.raises(ValueError, match='requires CARLA adapter'):
        wire.encode_failure('invalid historical profile', request=request.simulation_request(), adapter_version='0.5.0')
    with TestClient(worker.app) as client:
        assert client.post('/v3/inspect', json=request.model_dump(mode='json')).status_code == 422
        assert client.post('/v3/simulate', params={'store_id': store.id}, json=request.model_dump(mode='json')).status_code == 422
    # A fully rehashed private request still cannot broaden the old contract.
    with store.begin(request) as attempt:
        with pytest.raises(StoreUnavailable, match='identity/version'):
            attempt.verified_meta()
        with attempt.db() as db:
            assert db.execute('SELECT count(*) FROM archive').fetchone()[0] == 0
