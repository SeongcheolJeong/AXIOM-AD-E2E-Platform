"""Execution inputs and compute outlive DB sessions; publication owns evidence."""

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select

from ade2e.adapters import AdapterCancelled, AdapterOwnershipLost, Registry, supports_cancellation
from ade2e.database import ArtifactRow, Database, RunRow
from ade2e.reference import ReferenceAdapter
from ade2e.schemas import ArtifactPayload, RunCreate, ScenarioCreate, ScenarioPatch
from ade2e.service import (PreparedRun, cleanup_staged_run, compute_run, digest, latest_run_details,
                           prepare_run, publish_run, scenario_create, scenario_patch, stage_run)


@pytest.fixture
def execution(settings):
    db = Database(settings)
    db.initialize()
    registry = Registry(load_plugins=False)
    with db.session.begin() as session:
        scenario = scenario_create(session, ScenarioCreate(name="Detached engine", category="lane_follow"))
        body = RunCreate(scenario_id=scenario.id, seed=91, parameter_overrides={"initial_gap_m": 70})
        prepared = prepare_run(session, registry, body)
    yield db, registry, body, prepared
    db.engine.dispose()


def _assert_no_database(*_):
    raise AssertionError("Engine/staging must not perform database operations")


def test_prepared_input_survives_revision_change_and_adapter_mutation(execution):
    db, registry, body, prepared = execution
    original = prepared.to_payload()
    restored = PreparedRun.from_payload(json.loads(json.dumps(original)))
    assert restored == prepared
    assert restored.request_hash == digest(body.model_dump(mode="json"))
    with pytest.raises(FrozenInstanceError):
        prepared.request_hash = "0" * 64
    prepared.scenario.parameters.initial_gap_m = 1
    prepared.adapter_metadata.capabilities.clear()
    assert prepared.to_payload() == original

    with db.session.begin() as session:
        scenario_patch(session, body.scenario_id, ScenarioPatch(parameters={"initial_gap_m": 99}))

    class Mutating(ReferenceAdapter):
        def execute(self, scenario):
            assert db.engine.pool.checkedout() == 0
            result = super().execute(scenario)
            scenario.parameters.initial_gap_m = 1
            scenario.name = "Adapter local state"
            return result

    event.listen(db.engine, "before_cursor_execute", _assert_no_database)
    try:
        computed = compute_run(restored, Mutating(), run_id="run_reserved", created_at="2026-09-13T00:00:00.000000+00:00")
    finally:
        event.remove(db.engine, "before_cursor_execute", _assert_no_database)
    assert computed.run.id == "run_reserved"
    assert computed.run.created_at == "2026-09-13T00:00:00.000000+00:00"
    assert computed.run.scenario_snapshot.model_dump(mode="json") == original["scenario"]
    assert computed.run.provenance.input_hash == prepared.input_hash
    assert computed.run.scenario_revision == 1
    assert len(computed.run.metrics) == 5
    assert computed.run.status == "completed"

    with db.session.begin() as session:
        published = publish_run(session, db, restored, computed, "detached-idempotency")
    assert published.artifacts and computed.run.artifacts == []
    files = {path.name: path.read_bytes() for path in db.artifact_dir.iterdir()}
    with db.session.begin() as session:
        assert publish_run(session, db, restored, computed, "detached-idempotency") == published
    assert {path.name: path.read_bytes() for path in db.artifact_dir.iterdir()} == files


@pytest.mark.parametrize("changed", [{"version": "different"}, {"id": "different"}, {"status": "not_configured"}])
def test_changed_adapter_is_rejected_before_dispatch(execution, changed):
    db, registry, body, prepared = execution

    class Changed(ReferenceAdapter):
        def metadata(self):
            return super().metadata().model_copy(update=changed)

        def execute(self, scenario):
            raise AssertionError("Changed adapter must not be dispatched")

    with pytest.raises(HTTPException) as error:
        compute_run(prepared, Changed())
    assert error.value.status_code == 422
    with db.session() as session:
        assert list(session.scalars(select(RunRow))) == []


def test_cancellation_before_dispatch_does_not_execute(execution):
    db, registry, body, prepared = execution

    class Never(ReferenceAdapter):
        def execute(self, scenario):
            raise AssertionError("Cancelled before dispatch")

    with pytest.raises(AdapterCancelled, match="before dispatch"):
        compute_run(prepared, Never(), should_cancel=lambda: True)
    assert list(db.artifact_dir.iterdir()) == []


@pytest.mark.parametrize("stop", ["cancel", "lost_lease"])
def test_reference_checks_each_step_and_propagates_worker_control(execution, stop):
    db, registry, body, prepared = execution
    calls = 0

    def should_cancel():
        nonlocal calls
        calls += 1
        if calls == 5 and stop == "lost_lease":
            raise AdapterOwnershipLost("Lease was reclaimed")
        return calls == 5

    expected = AdapterCancelled if stop == "cancel" else AdapterOwnershipLost
    assert supports_cancellation(ReferenceAdapter())
    with pytest.raises(expected):
        compute_run(prepared, ReferenceAdapter(), should_cancel=should_cancel)
    assert calls == 5
    with db.session() as session:
        assert list(session.scalars(select(RunRow))) == []
    assert list(db.artifact_dir.iterdir()) == []


def test_legacy_execute_override_is_not_bypassed_or_discarded_by_late_cancel(execution):
    db, registry, body, prepared = execution
    cancelled = False

    class Legacy(ReferenceAdapter):
        def execute(self, scenario):
            nonlocal cancelled
            result = super().execute(scenario)
            result.artifact_payloads = [ArtifactPayload(name="proof.bin", kind="fixture", media_type="application/octet-stream", content=b"actual legacy result")]
            cancelled = True
            return result

    adapter = Legacy()
    assert not supports_cancellation(adapter)
    result = compute_run(prepared, adapter, should_cancel=lambda: cancelled)
    assert cancelled
    assert result.run.status == "completed"
    assert result.artifact_payloads[0].content == b"actual legacy result"


def test_explicit_cancellation_hook_is_used_and_deterministic(execution):
    db, registry, body, prepared = execution
    adapter = ReferenceAdapter()
    sync = compute_run(prepared, adapter)
    cooperative = compute_run(prepared, adapter, should_cancel=lambda: False)
    assert sync.run.provenance == cooperative.run.provenance
    assert sync.run.trajectory == cooperative.run.trajectory

    class Explicit(ReferenceAdapter):
        def execute(self, scenario):
            raise AssertionError("Use explicit cancellation hook")

        def execute_cancellable(self, scenario, should_cancel):
            raise AdapterCancelled("Explicit hook")

    assert supports_cancellation(Explicit())
    with pytest.raises(AdapterCancelled, match="Explicit hook"):
        compute_run(prepared, Explicit(), should_cancel=lambda: False)


def test_reserved_dispatch_time_does_not_reorder_when_old_compute_finishes_last(execution):
    db, registry, body, prepared = execution
    older = compute_run(prepared, ReferenceAdapter(), run_id="run_older", created_at="2026-09-13T00:00:00.000000+00:00")
    newer = compute_run(prepared, ReferenceAdapter(), run_id="run_newer", created_at="2026-09-13T00:00:01.000000+00:00")
    with db.session.begin() as session:
        publish_run(session, db, prepared, newer)
    with db.session.begin() as session:
        publish_run(session, db, prepared, older)
    with db.session() as session:
        assert latest_run_details(session)[body.scenario_id].id == "run_newer"


def test_staging_never_reads_database_or_rewrites_files_during_publish(execution, monkeypatch):
    db, registry, body, prepared = execution
    computed = compute_run(prepared, ReferenceAdapter())
    event.listen(db.engine, "before_cursor_execute", _assert_no_database)
    try:
        staged = stage_run(db, prepared, computed, "../../unsafe-ownership-label")
    finally:
        event.remove(db.engine, "before_cursor_execute", _assert_no_database)
    assert staged.directory.parent == db.artifact_dir / ".staging"
    with db.session() as session:
        assert list(session.scalars(select(ArtifactRow))) == []
    original_open = Path.open

    def no_blob_write(path, *args, **kwargs):
        if args and args[0] in {"wb", "xb"} and path.suffix == ".blob":
            raise AssertionError("Publication must promote, not copy bytes")
        return original_open(path, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, "open", no_blob_write)
            with db.session.begin() as session:
                published = publish_run(session, db, prepared, computed, staged=staged)
        assert published == staged.run
        for item in staged.artifacts:
            content = (db.artifact_dir / item.storage_key).read_bytes()
            assert hashlib.sha256(content).hexdigest() == item.artifact.sha256
            assert len(content) == item.artifact.size_bytes
    finally:
        cleanup_staged_run(db, staged)
    assert not staged.directory.exists()
    assert computed.run.artifacts == []


def test_staged_promotion_never_overwrites_an_existing_file(execution):
    db, registry, body, prepared = execution
    computed = compute_run(prepared, ReferenceAdapter())
    staged = stage_run(db, prepared, computed, "exclusive-promotion")
    target = db.artifact_dir / staged.artifacts[0].storage_key
    target.write_bytes(b"existing unrelated evidence")
    try:
        with pytest.raises(FileExistsError):
            with db.session.begin() as session:
                publish_run(session, db, prepared, computed, staged=staged)
    finally:
        cleanup_staged_run(db, staged)
    assert target.read_bytes() == b"existing unrelated evidence"
    with db.session() as session:
        assert list(session.scalars(select(RunRow))) == []


@pytest.mark.parametrize("failure", ["commit", "short_write"])
def test_failed_staging_or_commit_removes_only_this_attempt(execution, monkeypatch, failure):
    db, registry, body, prepared = execution
    first = compute_run(prepared, ReferenceAdapter())
    with db.session.begin() as session:
        publish_run(session, db, prepared, first)
    committed_files = {p.name: p.read_bytes() for p in db.artifact_dir.glob("*.blob")}
    other_attempt = stage_run(db, prepared, compute_run(prepared, ReferenceAdapter()), "other-worker")
    other_files = {p.name: p.read_bytes() for p in other_attempt.directory.iterdir()}
    computed = compute_run(prepared, ReferenceAdapter())

    if failure == "commit":
        staged = stage_run(db, prepared, computed, "failed-worker")

        def fail_commit(*_):
            raise OSError("Injected commit failure")

        event.listen(db.session.class_, "before_commit", fail_commit)
        try:
            with pytest.raises(OSError, match="commit failure"):
                with db.session.begin() as session:
                    publish_run(session, db, prepared, computed, staged=staged)
        finally:
            event.remove(db.session.class_, "before_commit", fail_commit)
            cleanup_staged_run(db, staged)
    else:
        original_open = Path.open

        class ShortStream:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.stream.close()

            def write(self, content):
                return self.stream.write(content[:7])

        def short_write(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return ShortStream(stream) if args and args[0] == "xb" and path.suffix == ".blob" else stream

        with monkeypatch.context() as patch:
            patch.setattr(Path, "open", short_write)
            with pytest.raises(OSError, match="Incomplete staged artifact write"):
                stage_run(db, prepared, computed, "failed-worker")
    assert {p.name: p.read_bytes() for p in db.artifact_dir.glob("*.blob")} == committed_files
    assert {p.name: p.read_bytes() for p in other_attempt.directory.iterdir()} == other_files
    assert list((db.artifact_dir / ".staging").iterdir()) == [other_attempt.directory]
    with db.session() as session:
        assert len(list(session.scalars(select(RunRow)))) == 1
    cleanup_staged_run(db, other_attempt)
