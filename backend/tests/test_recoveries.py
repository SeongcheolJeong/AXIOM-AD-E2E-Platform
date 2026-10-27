"""Durable retrieval lifecycle with a fake journal adapter, no simulator calls."""
import hashlib
import json
import threading
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select

from ade2e import jobs, service
from ade2e.adapters import AdapterExecutionError
from ade2e.execution_attempt import ExecutionAttemptRequest, RemoteExecutionArchive, RemoteExecutionStatus
from ade2e.job_models import EngineResourceRow, JobRow
from ade2e.database import RunRow, ArtifactRow
from ade2e.reference import ReferenceAdapter
from ade2e.recoveries import bind_attempt, submit_recovery, inspect_execution, execute_claim, resolved_job_run
from ade2e.recovery_models import RecoveryRow, RemoteAttemptRow
from ade2e.recovery_schemas import RecoveryCreate
from ade2e.schemas import RunCreate, AdapterFailureEvidence, ArtifactPayload
from ade2e.worker import run_once, run_claim
from test_jobs import context, clock
from test_simulation_context import context as live_context


class JournalAdapter(ReferenceAdapter):
    """Stores one sealed authored result, then simulates a lost HTTP response."""
    def __init__(self, db):
        self.db = db
        self.store_id = 'test-durable-store'
        self.saved = {}
        self.executions = self.retrievals = 0
        self.after_retrieve = None
        self.result_mutator = None
        self.failure = False
        self._transactions = {}
        event.listen(db.engine, 'begin', lambda connection: self._transactions.__setitem__(id(connection), threading.get_ident()))
        event.listen(db.engine, 'commit', lambda connection: self._transactions.pop(id(connection), None))
        event.listen(db.engine, 'rollback', lambda connection: self._transactions.pop(id(connection), None))

    def assert_detached(self):
        assert threading.get_ident() not in self._transactions.values()

    def metadata(self):
        return super().metadata().model_copy(update={'id': 'journal', 'version': '1', 'name': 'Authored journal fixture'})

    def execute(self, scenario):
        raise AssertionError('Attempt-aware execution must not fall back to execute')

    def execute_attempt(self, request, *, expected_store_id=None):
        self.assert_detached()
        assert expected_store_id == self.store_id
        assert request.execution_id not in self.saved
        self.executions += 1
        result = ReferenceAdapter.execute(self, request.scenario)
        result.artifact_payloads = [ArtifactPayload(name='source.txt', kind='authored_fixture', media_type='text/plain', content=b'original source bytes')]
        raw = service.canonical_bytes({'request': request.model_dump(mode='json'), 'output': result.model_dump(mode='json', exclude={'artifact_payloads'}), 'artifact': 'original source bytes'})
        self.saved[request.execution_id] = (request.model_copy(deep=True), raw, result)
        raise AdapterExecutionError('Fixture lost its HTTP response after sealing the result')

    def inspect_execution(self, request):
        self.assert_detached()
        saved = self.saved.get(request.execution_id)
        return RemoteExecutionStatus(store_id=self.store_id, execution_id=request.execution_id,
            request_hash=request.request_hash, input_hash=request.input_hash, context_hash=request.context_hash,
            adapter_id=request.adapter_id, adapter_version=request.adapter_version,
            state=('interrupted' if self.failure else 'completed') if saved else 'not_found', observed_at=service.utcnow(),
            archive_kind=('partial' if self.failure else 'complete') if saved else None,
            archive_sha256=hashlib.sha256(saved[1]).hexdigest() if saved else None,
            archive_size_bytes=len(saved[1]) if saved else None, mutations_started=bool(saved),
            cleanup_status='unknown' if self.failure else 'confirmed' if saved else 'not_started')

    def retrieve_execution(self, request, *, expected_store_id, expected_archive_sha256):
        self.assert_detached()
        assert expected_store_id == self.store_id
        self.retrievals += 1
        status = self.inspect_execution(request)
        assert expected_archive_sha256 == status.archive_sha256
        _, raw, result = self.saved[request.execution_id]
        result = result.model_copy(deep=True)
        if self.result_mutator:
            self.result_mutator(result)
        value = RemoteExecutionArchive(status=status, archive_bytes=raw,
            result=None if self.failure else result,
            failure=AdapterFailureEvidence(artifact_payloads=result.artifact_payloads) if self.failure else None,
            error='Interrupted fixture: committed diagnostic prefix only' if self.failure else None,
            execution_ms=12.5)
        if self.after_retrieve:
            self.after_retrieve()
        return value


@pytest.fixture
def remote(context):
    db, registry, body = context
    adapter = JournalAdapter(db)
    registry.register(adapter)
    body = body.model_copy(update={'adapter_id': 'journal'})
    source = jobs.submit_job(db, registry, body, 'job:lost')
    completed = run_once(db, registry, 'source-worker', job_id=source.id)
    assert completed.status == 'failed' and adapter.executions == 1
    return db, registry, adapter, body, completed


def recover(remote, key='retrieve'):
    db, registry, _, _, source = remote
    return submit_recovery(db, registry, RecoveryCreate(source_job_id=source.id, note='Retrieve without resimulation'), key)


def test_response_loss_retrieval_preserves_original_run_history_hashes_and_quarantine(remote):
    db, registry, adapter, body, source = remote
    with db.read_session() as session:
        original = service.run_get(session, source.run_id).model_dump(mode='json')
        source_bytes = service.canonical_bytes(jobs._row(session, source.id).prepared)
        reserved = jobs._row(session, source.id).reserved_run_id
    before = source.model_dump(mode='json')
    inspection = inspect_execution(db, registry, source.id)
    assert inspection.can_retrieve and inspection.status.state == 'completed'
    recovery = recover(remote)
    result = run_once(db, registry, 'retriever', job_id=recovery.id)
    assert result.status == 'completed' and result.outcome == 'recovered'
    assert adapter.executions == 1 and adapter.retrievals == 1
    assert jobs.job_get(db, source.id).model_dump(mode='json') == before
    with db.read_session() as session:
        run = service.run_get(session, result.run_id)
        assert run.id != source.run_id and run.recovery.source_execution_id == reserved == source.run_id
        assert run.created_at == source.created_at and run.execution_ms == 12.5
        assert run.recovery.source_submission_seq == source.submission_seq
        assert service.completed_run_integrity_error(run) is None
        assert all(item.run_id == run.id for item in run.artifacts)
        assert run.artifacts[0].sha256 == hashlib.sha256(b'original source bytes').hexdigest()
        assert service.run_get(session, source.run_id).model_dump(mode='json') == original
        assert service.canonical_bytes(jobs._row(session, source.id).prepared) == source_bytes
        assert session.get(EngineResourceRow, 'journal').state == 'reconcile_required'
        assert resolved_job_run(session, jobs._row(session, source.id)).id == run.id
        assert service.latest_run_details(session)[body.scenario_id].id == run.id
    assert jobs.submit_job(db, registry, body, 'job:lost').run_id == source.run_id
    registry._adapters.clear()
    assert recover(remote).id == recovery.id  # replay before adapter availability


def test_recovery_idempotency_conflict_and_unknown_original_capability(remote):
    db, registry, adapter, body, source = remote
    first = recover(remote)
    with pytest.raises(HTTPException) as info:
        submit_recovery(db, registry, RecoveryCreate(source_job_id=source.id, note='Different note'), 'retrieve')
    assert info.value.status_code == 409
    other = jobs.submit_job(db, registry, RunCreate(scenario_id=body.scenario_id), 'local')
    local = run_once(db, registry, 'local-worker', job_id=other.id)
    assert not inspect_execution(db, registry, local.id).supported
    assert jobs.job_list(db)['items'][0].id == other.id  # retrieval excluded from driving jobs


def test_partial_archive_never_becomes_completed_validation(remote):
    db, registry, adapter, _, _ = remote
    adapter.failure = True
    done = run_once(db, registry, 'retriever', job_id=recover(remote).id)
    assert done.status == 'completed' and done.outcome == 'diagnostics_only'
    with db.read_session() as session:
        run = service.run_get(session, done.run_id)
        assert run.status == 'error' and run.metrics == [] and run.trajectory == []
        assert run.recovery.archive_kind == 'partial'
        assert run.artifacts[0].sha256 == hashlib.sha256(b'original source bytes').hexdigest()
        assert service.latest_run_details(session)[run.scenario_id] is None


def test_no_terminal_archive_is_explicit_not_available_and_never_executes(remote):
    db, registry, adapter, _, _ = remote
    adapter.saved.clear()
    done = run_once(db, registry, 'retriever', job_id=recover(remote).id)
    assert done.status == 'completed' and done.outcome == 'not_available' and done.run_id is None
    assert adapter.executions == 1 and adapter.retrievals == 0


def test_store_replacement_blocks_retrieval(remote):
    db, registry, adapter, _, source = remote
    recovery = recover(remote)
    adapter.store_id = 'different-store'
    assert not inspect_execution(db, registry, source.id).can_retrieve
    done = run_once(db, registry, 'retriever', job_id=recovery.id)
    assert done.status == 'failed' and done.run_id is None and adapter.retrievals == 0


def test_metrics_from_archive_are_revalidated_without_new_engine_call(remote):
    db, registry, adapter, _, _ = remote
    adapter.result_mutator = lambda result: setattr(result.metrics[0], 'passed', not result.metrics[0].passed)
    done = run_once(db, registry, 'retriever', job_id=recover(remote).id)
    assert done.status == 'failed' and done.run_id is None
    assert adapter.executions == 1
    assert list(db.recovery_dir.iterdir()) == []
    assert list(db.recovery_staging_dir.iterdir()) == []


def test_lost_retrieval_lease_cannot_publish_and_source_quarantine_remains(remote, clock):
    db, registry, adapter, _, source = remote
    recovery = recover(remote)
    claim = jobs.claim_job(db, 'retriever', job_id=recovery.id, capabilities={('recovery', 'journal', '1')})
    adapter.after_retrieve = lambda: clock(60)
    done = run_claim(db, registry, claim)
    assert done.status == 'interrupted' and done.run_id is None
    with db.read_session() as session:
        assert session.scalar(select(func.count()).select_from(RunRow)) == 1
        assert session.get(RemoteAttemptRow, source.id).recovered_run_id is None
        assert session.get(EngineResourceRow, 'journal').state == 'reconcile_required'


def test_cancel_after_get_discards_unpublished_archive_and_keeps_original(remote):
    db, registry, adapter, _, _ = remote
    recovery = recover(remote)
    adapter.after_retrieve = lambda: jobs.cancel_job(db, recovery.id, kind='recovery')
    # Direct claim callback avoids worker's intentional 50ms cancellation cache.
    claim = jobs.claim_job(db, 'retriever', job_id=recovery.id, capabilities={('recovery', 'journal', '1')})
    jobs.mark_dispatched(db, claim)
    from ade2e.adapters import AdapterCancelled
    with pytest.raises(AdapterCancelled):
        execute_claim(db, claim, adapter, lambda: jobs.cancellation_requested(db, claim))
    jobs.fail_job(db, claim, 'Read-only retrieval cancelled', status='cancelled')
    assert jobs.job_get(db, recovery.id, kind='recovery').run_id is None
    assert list(db.recovery_dir.iterdir()) == []


def test_publication_commit_failure_removes_only_owned_files(remote):
    db, registry, adapter, _, source = remote
    before = {p: p.read_bytes() for p in db.artifact_dir.rglob('*') if p.is_file()}
    recovery = recover(remote)
    claim = jobs.claim_job(db, 'retriever', job_id=recovery.id, capabilities={('recovery', 'journal', '1')})
    jobs.mark_dispatched(db, claim)
    def fail(session):
        if session.scalar(select(func.count()).select_from(RunRow).where(RunRow.id != source.run_id)):
            raise RuntimeError('Injected recovery publication commit failure')
    event.listen(db.session.class_, 'before_commit', fail)
    try:
        with pytest.raises(RuntimeError, match='publication commit'):
            execute_claim(db, claim, adapter, lambda: False)
    finally:
        event.remove(db.session.class_, 'before_commit', fail)
    assert {p: p.read_bytes() for p in db.artifact_dir.rglob('*') if p.is_file()} == before
    assert list(db.recovery_dir.iterdir()) == [] and list(db.recovery_staging_dir.iterdir()) == []
    with db.read_session() as session:
        assert session.get(RemoteAttemptRow, source.id).recovered_run_id is None


def test_archive_temp_creation_failure_cleans_previously_staged_run(remote, monkeypatch):
    db, registry, adapter, _, _ = remote
    original = {p: p.read_bytes() for p in db.artifact_dir.rglob('*') if p.is_file()}
    queued = recover(remote)
    claim = jobs.claim_job(db, 'retriever', job_id=queued.id, capabilities={('recovery', 'journal', '1')})
    jobs.mark_dispatched(db, claim)
    def no_space(*args, **kwargs):
        assert any(p.is_file() for p in (db.artifact_dir / '.staging').rglob('*'))
        raise OSError('Injected recovery staging disk quota failure')
    monkeypatch.setattr('ade2e.recoveries.tempfile.mkstemp', no_space)
    with pytest.raises(OSError, match='disk quota'):
        execute_claim(db, claim, adapter, lambda: False)
    assert {p: p.read_bytes() for p in db.artifact_dir.rglob('*') if p.is_file()} == original
    assert list(db.recovery_dir.iterdir()) == [] and list(db.recovery_staging_dir.iterdir()) == []


def test_newer_dispatch_blocks_late_older_recovery(remote):
    db, registry, adapter, body, source = remote
    pending = recover(remote)
    # Explicit operator reconciliation is separate from evidence retrieval.
    jobs.reconcile_resource(db, 'journal', source.id, 'Fixture remote actors acknowledged stopped', confirmed_stopped=True)
    newer = jobs.submit_job(db, registry, body, 'newer')
    newer = run_once(db, registry, 'new-worker', job_id=newer.id)
    done = run_once(db, registry, 'retriever', job_id=pending.id)
    assert done.outcome == 'recovered'
    with db.read_session() as session:
        assert service.latest_run_details(session)[body.scenario_id] is None
        assert jobs.latest_job_evidence(session)[body.scenario_id].id == newer.id


def test_tampered_recovered_run_or_sealed_summary_is_rejected(remote):
    db, registry, _, _, _ = remote
    done = run_once(db, registry, 'retriever', job_id=recover(remote).id)
    with db.session.begin() as session:
        row = session.get(RunRow, done.run_id)
        changed = deepcopy(row.payload)
        changed['recovery']['source_execution_id'] = 'wrong-execution'
        row.payload = changed
    with db.read_session() as session, pytest.raises(HTTPException) as info:
        service.run_get(session, done.run_id)
    assert info.value.status_code == 409


def test_attempt_hash_retains_old_input_hash_and_execution_id_is_transport_only(remote):
    db, _, _, _, source = remote
    with db.read_session() as session:
        row = jobs._row(session, source.id)
        prepared = service.PreparedRun.from_payload(row.prepared)
    first = service.execution_attempt_request(prepared, 'run_first')
    second = first.model_copy(update={'execution_id': 'run_second'})
    assert first.input_hash == second.input_hash == source.input_hash
    assert first.request_hash != second.request_hash
    assert set(prepared.to_payload()) == {'scenario', 'adapter_metadata', 'request_hash'}


def test_worker_death_without_original_run_recovers_same_reserved_identity(context, clock):
    db, registry, body = context
    adapter = JournalAdapter(db)
    registry.register(adapter)
    source = jobs.submit_job(db, registry, body.model_copy(update={'adapter_id': 'journal'}), 'worker-death')
    claim = jobs.claim_job(db, 'lost-worker', job_id=source.id)
    attempt = service.execution_attempt_request(claim.prepared, claim.reserved_run_id)
    store = bind_attempt(db, claim, adapter.inspect_execution(attempt))
    jobs.mark_dispatched(db, claim)
    with pytest.raises(AdapterExecutionError):
        adapter.execute_attempt(attempt, expected_store_id=store)
    clock(40)
    assert jobs.expire_jobs(db) == 1
    source = jobs.job_get(db, source.id)
    assert source.status == 'interrupted' and source.run_id is None
    recovery = submit_recovery(db, registry, RecoveryCreate(source_job_id=source.id, note='Retain worker-death result'), 'retrieve-dead')
    done = run_once(db, registry, 'new-retriever', job_id=recovery.id)
    assert done.outcome == 'recovered'
    with db.read_session() as session:
        run = service.run_get(session, done.run_id)
        assert run.recovery.source_run_id is None
        assert run.recovery.source_execution_id == claim.reserved_run_id
    with pytest.raises(jobs.LostLease):
        jobs.finish_job(db, claim, service.ComputedRun(run, ()))


def test_concurrent_duplicate_retrievals_publish_one_run_without_engine_claim(remote):
    from concurrent.futures import ThreadPoolExecutor
    db, registry, adapter, _, source = remote
    first, second = recover(remote, 'a'), recover(remote, 'b')
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(run_once, db, registry, f'retriever-{i}', job_id=job.id)
                   for i, job in enumerate((first, second))]
        results = [f.result(timeout=15) for f in futures]
    assert all(result.status == 'completed' for result in results)
    assert results[0].run_id == results[1].run_id
    assert adapter.executions == 1
    with db.read_session() as session:
        assert session.scalar(select(func.count()).select_from(RunRow)) == 2
        assert session.get(EngineResourceRow, 'journal').job_id == source.id


def test_live_artifact_execution_identity_survives_new_local_storage_run(live_context):
    _, db, registry, body, context_adapter, *_ = live_context
    class LiveJournal(JournalAdapter):
        def metadata(self):
            return context_adapter.metadata()
        def validate_execution_context(self, scenario, context):
            return context_adapter.validate_execution_context(scenario, context)
        def execute_with_context(self, request):
            raise AssertionError('No fallback to context-only execution')
        def execute_attempt(self, request, *, expected_store_id=None):
            assert expected_store_id == self.store_id
            self.executions += 1
            result = context_adapter.execute_with_context(request.simulation_request())
            self.saved[request.execution_id] = (request, b'Authored live journal archive; fixture codec', result)
            raise AdapterExecutionError('Lost complete live response')
    adapter = LiveJournal(db)
    registry.register(adapter, replace=True)
    source = jobs.submit_job(db, registry, body, 'live-journal')
    source = run_once(db, registry, 'source', job_id=source.id)
    original_artifacts = adapter.saved[source.run_id][2].artifact_payloads
    queued = submit_recovery(db, registry, RecoveryCreate(source_job_id=source.id, note='Retrieve original live bytes'), 'live')
    done = run_once(db, registry, 'retriever', job_id=queued.id)
    assert done.outcome == 'recovered', done
    with db.read_session() as session:
        recovered = service.run_get(session, done.run_id)
        assert recovered.recovery.source_execution_id == source.run_id != recovered.id
        for original, stored in zip(original_artifacts, recovered.artifacts):
            row = session.get(ArtifactRow, stored.id)
            assert (db.artifact_dir / row.storage_key).read_bytes() == original.content
        assert service.completed_run_integrity_error(recovered) is None
    assert adapter.executions == len(context_adapter.requests) == 1
