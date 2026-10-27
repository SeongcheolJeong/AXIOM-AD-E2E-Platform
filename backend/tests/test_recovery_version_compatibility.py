"""Read old attempts after an adapter upgrade without widening execution rights.

The journal fixture seals authored reference output and loses its response. These
are core queue/codec contract checks, with no CARLA dependency or simulator run.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ade2e import execution_attempt, jobs, service
from ade2e.recoveries import execute_claim, inspect_execution, submit_recovery
from ade2e.recovery_models import RecoveryRow, RemoteAttemptRow
from ade2e.recovery_schemas import RecoveryCreate
from ade2e.worker import run_claim, run_once
from test_jobs import context  # noqa: F401 - fixture dependency of remote
from test_recoveries import JournalAdapter, recover, remote  # noqa: F401


def _adapter_without_declaration(version='2'):
    return SimpleNamespace(
        metadata=lambda: SimpleNamespace(version=version),
        execute_attempt=lambda *args, **kwargs: None,
        inspect_execution=lambda *args, **kwargs: None,
        retrieve_execution=lambda *args, **kwargs: None,
    )


def _install_reader(monkeypatch, adapter, version, versions):
    metadata = adapter.metadata().model_copy(update={'version': version})
    monkeypatch.setattr(adapter, 'metadata', lambda: metadata)
    monkeypatch.setattr(adapter, 'remote_recovery_versions', lambda: versions, raising=False)


def _observe_inspections(monkeypatch, adapter):
    requests = []
    inspect = adapter.inspect_execution

    def observed(request):
        requests.append(request.model_dump(mode='json'))
        return inspect(request)

    monkeypatch.setattr(adapter, 'inspect_execution', observed)
    return requests


def _source_snapshot(db, source):
    """Exclude the supplemental recovery link; keep original evidence exact."""
    with db.read_session() as session:
        job = jobs._row(session, source.id)
        binding = session.get(RemoteAttemptRow, source.id)
        return service.canonical_bytes({
            'job': jobs.job_get(db, source.id).model_dump(mode='json'),
            'run': service.run_get(session, source.run_id).model_dump(mode='json'),
            'prepared': job.prepared,
            'binding': binding.payload,
            'binding_hash': binding.payload_hash,
        })


def test_legacy_plugin_recovery_defaults_to_only_its_installed_version():
    adapter = _adapter_without_declaration()
    assert execution_attempt.remote_recovery_versions(adapter) == ('2',)
    assert execution_attempt.supports_remote_recovery(adapter)
    assert execution_attempt.supports_remote_recovery(adapter, version='2')
    assert not execution_attempt.supports_remote_recovery(adapter, version='1')


@pytest.mark.parametrize('declaration', [('1', '2'), ['1', '2']])
def test_explicit_recovery_versions_preserve_the_declared_order(declaration):
    adapter = _adapter_without_declaration()
    adapter.remote_recovery_versions = lambda: declaration
    assert execution_attempt.remote_recovery_versions(adapter) == ('1', '2')
    assert execution_attempt.supports_remote_recovery(adapter, version='1')
    assert execution_attempt.supports_remote_recovery(adapter, version='2')
    assert not execution_attempt.supports_remote_recovery(adapter, version='3')


@pytest.mark.parametrize('declaration', [
    None, '1', {'1': True}, {'1', '2'}, (), [], ('1', '1'),
    ('',), (' ',), (' 1',), ('1 ',), (1,), (True,), (None,),
    ('x' * 181,), tuple(str(index) for index in range(33)),
])
def test_invalid_recovery_version_declarations_fail_closed(declaration):
    adapter = _adapter_without_declaration()
    adapter.remote_recovery_versions = lambda: declaration
    assert execution_attempt.remote_recovery_versions(adapter) == ()
    assert not execution_attempt.supports_remote_recovery(adapter)
    assert not execution_attempt.supports_remote_recovery(adapter, version='2')


def test_a_broken_plugin_version_hook_does_not_fall_back_to_current_version():
    adapter = _adapter_without_declaration()

    def broken():
        raise RuntimeError('Fixture cannot declare its recovery codec versions')

    adapter.remote_recovery_versions = broken
    assert execution_attempt.remote_recovery_versions(adapter) == ()
    assert not execution_attempt.supports_remote_recovery(adapter, version='2')


@pytest.mark.parametrize('missing', ['execute_attempt', 'inspect_execution', 'retrieve_execution'])
def test_version_declaration_never_substitutes_for_required_recovery_hooks(missing):
    adapter = _adapter_without_declaration()
    adapter.remote_recovery_versions = lambda: ('1', '2')
    setattr(adapter, missing, None)
    assert execution_attempt.remote_recovery_versions(adapter) == ()
    assert not execution_attempt.supports_remote_recovery(adapter, version='1')


@pytest.mark.parametrize('queued_before_upgrade', [True, False], ids=['old-reader-queued', 'new-reader-queued'])
def test_upgraded_reader_recovers_old_source_without_rewriting_or_reexecution(
        remote, monkeypatch, queued_before_upgrade):
    db, registry, adapter, _, source = remote
    before = _source_snapshot(db, source)
    with db.read_session() as session:
        source_prepared = service.canonical_bytes(jobs._row(session, source.id).prepared)
    original_request, original_archive, _ = adapter.saved[source.run_id]
    original_request_bytes = service.canonical_bytes(original_request.model_dump(mode='json'))
    assert source.adapter_version == original_request.adapter_version == '1'

    if queued_before_upgrade:
        queued = recover(remote)
    _install_reader(monkeypatch, adapter, '2', ('1', '2'))
    if not queued_before_upgrade:
        queued = recover(remote)
    inspections = _observe_inspections(monkeypatch, adapter)
    expected_reader = '1' if queued_before_upgrade else '2'
    with db.read_session() as session:
        assert jobs._row(session, queued.id).adapter_version == expected_reader
    assert queued.binding.attempt.adapter_version == '1'

    result = run_once(db, registry, 'upgraded-read-only-worker', job_id=queued.id)
    assert result is not None, 'An upgraded reader must claim supported old queued recoveries'
    assert result.status == 'completed' and result.outcome == 'recovered'
    assert result.binding.attempt.adapter_version == result.remote_status.adapter_version == '1'
    assert adapter.metadata().version == '2'
    assert adapter.executions == adapter.retrievals == 1
    assert inspections and all(service.canonical_bytes(value) == original_request_bytes for value in inspections)
    assert _source_snapshot(db, source) == before
    assert adapter.saved[source.run_id][1] == original_archive

    with db.read_session() as session:
        run = service.run_get(session, result.run_id)
        assert run.provenance.adapter_version == '1' and run.provenance.input_hash == source.input_hash
        assert run.recovery.source_execution_id == source.run_id
        assert service.completed_run_integrity_error(run) is None
        assert service.canonical_bytes(jobs._row(session, source.id).prepared) == source_prepared
        reader_job = jobs._row(session, queued.id)
        assert reader_job.adapter_version == expected_reader
        assert reader_job.prepared['binding']['attempt']['adapter_version'] == '1'
        archive = session.get(RecoveryRow, queued.id)
        assert (db.recovery_dir / archive.archive_storage_key).read_bytes() == original_archive

    repeated = recover(remote)
    assert repeated.id == result.id and repeated.run_id == result.run_id
    assert run_once(db, registry, 'second-reader', job_id=queued.id) is None
    assert adapter.executions == adapter.retrievals == 1


def test_unsupported_source_version_blocks_inspection_and_new_recovery_submission(remote, monkeypatch):
    db, registry, adapter, _, source = remote
    _install_reader(monkeypatch, adapter, '2', ('2',))
    inspections = _observe_inspections(monkeypatch, adapter)
    before = _source_snapshot(db, source)

    reply = inspect_execution(db, registry, source.id)
    assert not reply.supported and not reply.can_inspect and not reply.can_retrieve
    assert reply.status is None and reply.reason
    with pytest.raises(HTTPException) as error:
        submit_recovery(db, registry, RecoveryCreate(source_job_id=source.id, note='Unsupported old source'), 'unsupported')
    assert error.value.status_code == 422
    assert inspections == [] and adapter.retrievals == 0 and adapter.executions == 1
    assert _source_snapshot(db, source) == before


def test_worker_does_not_claim_recovery_for_an_unsupported_reader_version(remote, monkeypatch):
    db, registry, adapter, _, _ = remote
    queued = recover(remote)
    _install_reader(monkeypatch, adapter, '2', ('2',))
    inspections = _observe_inspections(monkeypatch, adapter)
    assert run_once(db, registry, 'incompatible-reader', job_id=queued.id) is None
    assert jobs.job_get(db, queued.id, kind='recovery').status == 'queued'
    assert inspections == [] and adapter.retrievals == 0 and adapter.executions == 1


@pytest.mark.parametrize('unsupported', ['source', 'reader'])
def test_claimed_recovery_rechecks_both_source_and_reader_versions_before_external_io(
        remote, monkeypatch, unsupported):
    db, registry, adapter, _, source = remote
    _install_reader(monkeypatch, adapter, '2', ('1', '2'))
    queued = recover(remote)
    claim = jobs.claim_job(db, 'claimed-before-codec-change', job_id=queued.id,
                           capabilities={('recovery', 'journal', '2')})
    assert claim is not None
    # Each case keeps the other version supported. This catches a guard that
    # validates only the source or only the accepted reader version.
    _install_reader(monkeypatch, adapter, '3', ('2', '3') if unsupported == 'source' else ('1', '3'))
    inspections = _observe_inspections(monkeypatch, adapter)
    before = _source_snapshot(db, source)

    result = run_claim(db, registry, claim)
    assert result.status == 'failed' and result.run_id is None
    assert inspections == [] and adapter.retrievals == 0 and adapter.executions == 1
    assert _source_snapshot(db, source) == before
    with db.read_session() as session:
        job = jobs._row(session, queued.id)
        assert job.dispatched_at is None and not job.outcome_uncertain
        assert job.failure_http_status == 422
        assert job.adapter_version == '2'


@pytest.mark.parametrize('unsupported', ['source', 'reader'])
def test_direct_recovery_rechecks_versions_after_dispatch_intent(remote, monkeypatch, unsupported):
    db, registry, adapter, _, source = remote
    _install_reader(monkeypatch, adapter, '2', ('1', '2'))
    queued = recover(remote)
    claim = jobs.claim_job(db, 'reader-before-codec-change', job_id=queued.id,
                           capabilities={('recovery', 'journal', '2')})
    assert claim is not None and jobs.mark_dispatched(db, claim)
    _install_reader(monkeypatch, adapter, '3', ('2', '3') if unsupported == 'source' else ('1', '3'))
    inspections = _observe_inspections(monkeypatch, adapter)
    before = _source_snapshot(db, source)

    with pytest.raises(HTTPException) as error:
        execute_claim(db, claim, adapter, lambda: False)
    assert error.value.status_code == 422
    assert inspections == [] and adapter.retrievals == 0 and adapter.executions == 1
    assert _source_snapshot(db, source) == before
    assert jobs.job_get(db, queued.id, kind='recovery').run_id is None
    assert list(db.recovery_dir.iterdir()) == []
    assert list(db.recovery_staging_dir.iterdir()) == []


@pytest.mark.parametrize('claimed_before_upgrade', [False, True], ids=['queued', 'claimed'])
def test_old_recovery_codec_support_never_authorizes_old_version_simulation(
        context, monkeypatch, claimed_before_upgrade):
    db, registry, body = context
    adapter = JournalAdapter(db)
    registry.register(adapter)
    body = body.model_copy(update={'adapter_id': 'journal'})
    old_job = jobs.submit_job(db, registry, body, 'old-simulation')
    claim = jobs.claim_job(db, 'old-execution-worker', job_id=old_job.id) if claimed_before_upgrade else None
    _install_reader(monkeypatch, adapter, '2', ('1', '2'))
    inspections = _observe_inspections(monkeypatch, adapter)

    if claimed_before_upgrade:
        assert claim is not None
        result = run_claim(db, registry, claim)
        assert result.status == 'failed' and not result.outcome_uncertain
        with db.read_session() as session:
            assert jobs._row(session, old_job.id).dispatched_at is None
    else:
        assert run_once(db, registry, 'new-execution-worker', job_id=old_job.id) is None
        assert jobs.job_get(db, old_job.id).status == 'queued'
    assert adapter.executions == adapter.retrievals == 0 and inspections == []
    assert adapter.saved == {}

    if not claimed_before_upgrade:
        new_job = jobs.submit_job(db, registry, body, 'new-simulation')
        assert new_job.adapter_version == '2'
        result = run_once(db, registry, 'new-execution-worker', job_id=new_job.id)
        assert result.status == 'failed'  # The fixture seals output, then loses the response.
        assert adapter.executions == 1 and adapter.retrievals == 0
        assert adapter.saved[result.run_id][0].adapter_version == '2'
