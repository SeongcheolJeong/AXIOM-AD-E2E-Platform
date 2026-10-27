"""Durable queue state, fencing and atomic publication independent of the UI."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import multiprocessing
from pathlib import Path

from fastapi import HTTPException
import pytest
from sqlalchemy import event, func, inspect, select

from ade2e import jobs, service
from ade2e.adapters import Registry
from ade2e.database import ArtifactRow, Database, IdempotencyRow, RunRow, SchemaVersionRow, Settings, SCHEMA_VERSION
from ade2e.job_models import EngineResourceRow, JobEventRow, JobRow, WorkerRow
from ade2e.job_schemas import JobDetail
from ade2e.reference import ReferenceAdapter
from ade2e.schemas import RunCreate, ScenarioCreate, ScenarioPatch


@pytest.fixture
def context(settings):
    db = Database(settings)
    db.initialize()
    registry = Registry(load_plugins=False)
    with db.session.begin() as session:
        scenario = service.scenario_create(session, ScenarioCreate(name="Durable queue fixture", duration_s=2, parameters={"trigger_time_s": 1}))
    yield db, registry, RunCreate(scenario_id=scenario.id)
    db.engine.dispose()


@pytest.fixture
def clock(monkeypatch):
    value = [datetime.now(timezone.utc)]
    monkeypatch.setattr(jobs, "utcnow", lambda: value[0].isoformat(timespec="microseconds"))

    def advance(seconds):
        value[0] += timedelta(seconds=seconds)

    return advance


class ExternalAdapter(ReferenceAdapter):
    def metadata(self):
        return super().metadata().model_copy(update={"id": "external", "name": "Test external engine"})


def submit(context, key="job:example", external=False):
    db, registry, body = context
    if external:
        registry.register(ExternalAdapter(), replace=True)
        body = body.model_copy(update={"adapter_id": "external"})
    return jobs.submit_job(db, registry, body, key)


def claimed(context, *, external=False, key="job:example"):
    db, registry, _ = context
    job = submit(context, key, external)
    claim = jobs.claim_job(db, "worker-1", job_id=job.id)
    assert claim is not None
    return claim


def computed(context, claim):
    _, registry, _ = context
    return service.compute_run(claim.prepared, registry.get(claim.job.adapter_id), run_id=claim.reserved_run_id, created_at=claim.job.created_at)


def test_submission_pins_inputs_and_idempotent_replay_precedes_current_state(context):
    db, registry, body = context
    original = submit(context)
    with db.session.begin() as session:
        service.scenario_patch(session, body.scenario_id, ScenarioPatch(seed=987, parameters={"ego_speed_mps": 3}))
    registry._adapters.clear()
    repeated = jobs.submit_job(db, registry, body, "job:example")
    assert repeated.id == original.id
    assert repeated.scenario_snapshot == original.scenario_snapshot
    claim = jobs.claim_job(db, "worker")
    assert claim.prepared.input_hash == original.input_hash
    assert claim.prepared.scenario == original.scenario_snapshot
    assert claim.job.run_id is None
    assert claim.reserved_run_id.startswith("run_")
    with pytest.raises(HTTPException) as error:
        jobs.submit_job(db, registry, body.model_copy(update={"seed": 123}), "job:example")
    assert error.value.status_code == 409


def test_saved_request_roundtrips_and_replays_with_same_canonical_hash(context):
    db, registry, body = context
    original = submit(context)
    serialized = original.model_dump(mode="json")
    restored = JobDetail.model_validate(serialized)
    replay = RunCreate.model_validate(serialized["request"])
    assert restored.id == original.id
    assert replay == body
    assert jobs.submit_job(db, registry, replay, "job:example").id == original.id


def test_queued_jobs_survive_database_reopen(context):
    db, _, _ = context
    original = submit(context)
    restarted = Database(Settings(data_dir=db.artifact_dir.parent, seed_examples=False))
    try:
        restarted.initialize()
        assert jobs.job_get(restarted, original.id).status == "queued"
        assert jobs.claim_job(restarted, "restarted").job.id == original.id
    finally:
        restarted.engine.dispose()


def test_claim_and_dispatch_are_each_single_use(context):
    db, _, _ = context
    claim = claimed(context)
    assert jobs.claim_job(db, "other", job_id=claim.job.id) is None
    assert jobs.mark_dispatched(db, claim)
    with pytest.raises(HTTPException) as error:
        jobs.mark_dispatched(db, claim)
    assert error.value.status_code == 409


def test_heartbeat_never_revives_expired_owner(context, clock):
    db, _, _ = context
    claim = claimed(context)
    clock(29)
    assert jobs.heartbeat_job(db, claim, 30)
    clock(31)
    assert not jobs.heartbeat_job(db, claim, 30)
    with pytest.raises(jobs.LostLease):
        jobs.cancellation_requested(db, claim)
    assert jobs.expire_jobs(db) == 1
    assert jobs.expire_jobs(db) == 0
    assert jobs.job_get(db, claim.job.id).status == "interrupted"
    assert jobs.claim_job(db, "other") is None


def test_wrong_token_cannot_dispatch_cancel_read_or_fail(context):
    db, _, _ = context
    claim = replace(claimed(context), token="wrong-token")
    assert not jobs.heartbeat_job(db, claim, 30)
    for operation in (jobs.mark_dispatched, jobs.cancellation_requested):
        with pytest.raises(jobs.LostLease):
            operation(db, claim)
    with pytest.raises(jobs.LostLease):
        jobs.fail_job(db, claim, "stale worker error")


def test_queued_cancel_is_terminal_without_claim_or_run(context):
    db, _, _ = context
    job = submit(context)
    cancelled = jobs.cancel_job(db, job.id)
    assert cancelled.status == "cancelled" and cancelled.dispatched_at is None
    assert jobs.claim_job(db, "worker") is None
    assert jobs.cancel_job(db, job.id).events_total == cancelled.events_total
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(RunRow)) == 0


def test_predispatch_cancel_releases_reserved_resource(context):
    db, _, _ = context
    claim = claimed(context, external=True)
    assert jobs.cancel_job(db, claim.job.id).status == "cancel_requested"
    assert jobs.mark_dispatched(db, claim) is False
    job = jobs.job_get(db, claim.job.id)
    assert job.status == "cancelled" and not job.outcome_uncertain
    assert jobs.list_resources(db)[0]["state"] == "idle"
    with db.session() as session:
        assert jobs.latest_job_evidence(session) == {}


def test_dispatched_cancellation_requires_actual_acknowledgement(context):
    db, _, _ = context
    claim = claimed(context)
    jobs.mark_dispatched(db, claim)
    with pytest.raises(HTTPException):
        jobs.fail_job(db, claim, "no request", status="cancelled")
    assert jobs.cancel_job(db, claim.job.id).status == "cancel_requested"
    assert jobs.cancellation_requested(db, claim)
    result = jobs.fail_job(db, claim, "Reference acknowledged stop", status="cancelled")
    assert result.status == "cancelled" and result.run_id is None and not result.outcome_uncertain
    with db.session() as session:
        assert jobs.latest_job_evidence(session)[claim.job.scenario_id].id == claim.job.id


def test_complete_result_after_cancel_is_retained_and_labelled(context):
    db, _, _ = context
    claim = claimed(context, external=True)
    jobs.mark_dispatched(db, claim)
    result = computed(context, claim)
    jobs.cancel_job(db, claim.job.id)
    finished = jobs.finish_job(db, claim, result)
    assert finished.status == "completed" and finished.run_id == claim.reserved_run_id
    assert any(event.type == "cancel_not_honoured" for event in finished.events)
    assert jobs.list_resources(db)[0]["state"] == "idle"


def test_resource_block_skips_to_other_adapter_and_quarantine_requires_reconcile(context, clock):
    db, _, _ = context
    first = claimed(context, external=True)
    jobs.mark_dispatched(db, first)
    external = submit(context, "job:external-2", external=True)
    reference = submit(context, "job:reference")
    assert jobs.claim_job(db, "other").job.id == reference.id
    assert jobs.claim_job(db, "blocked", job_id=external.id) is None
    clock(31)
    jobs.expire_jobs(db)
    assert jobs.list_resources(db)[0]["state"] == "reconcile_required"
    assert jobs.claim_job(db, "still-blocked", job_id=external.id) is None
    with pytest.raises(HTTPException):
        jobs.reconcile_resource(db, "external", first.job.id, "health was ready", confirmed_stopped=False)
    with pytest.raises(HTTPException):
        jobs.reconcile_resource(db, "external", external.id, "wrong job", confirmed_stopped=True)
    jobs.reconcile_resource(db, "external", first.job.id, "Old dispatcher stopped and exclusive engine reset", confirmed_stopped=True)
    old = jobs.job_get(db, first.job.id)
    assert old.status == "interrupted" and old.outcome_uncertain
    assert jobs.claim_job(db, "next", job_id=external.id).job.id == external.id


def test_exact_reference_class_can_run_in_parallel_but_subclass_is_gated(context):
    db, _, _ = context
    one, two = submit(context), submit(context, "job:two")
    assert jobs.claim_job(db, "one").job.id == one.id
    assert jobs.claim_job(db, "two").job.id == two.id
    assert jobs.list_resources(db) == []


def test_late_result_cannot_publish_after_expiry(context, clock):
    db, _, _ = context
    claim = claimed(context)
    jobs.mark_dispatched(db, claim)
    result = computed(context, claim)
    clock(31)
    with pytest.raises(jobs.LostLease):
        jobs.finish_job(db, claim, result)
    assert not list(db.artifact_dir.glob("*.blob"))
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(RunRow)) == 0


def test_expiry_during_staging_fences_publication_and_cleans_own_stage(context, clock, monkeypatch):
    db, _, _ = context
    claim = claimed(context)
    jobs.mark_dispatched(db, claim)
    original = service.stage_run

    def slow_stage(*args):
        staged = original(*args)
        clock(31)
        return staged

    monkeypatch.setattr(service, "stage_run", slow_stage)
    with pytest.raises(jobs.LostLease):
        jobs.finish_job(db, claim, computed(context, claim))
    assert not list(db.artifact_dir.glob("*.blob"))
    assert not list((db.artifact_dir / ".staging").iterdir())


def test_expiry_during_publish_rolls_back_run_files_and_terminal_transition(context, clock, monkeypatch):
    db, _, _ = context
    claim = claimed(context)
    jobs.mark_dispatched(db, claim)
    original = service.publish_run

    def late_publish(*args, **kwargs):
        run = original(*args, **kwargs)
        clock(31)
        return run

    monkeypatch.setattr(service, "publish_run", late_publish)
    with pytest.raises(jobs.LostLease):
        jobs.finish_job(db, claim, computed(context, claim))
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(RunRow)) == 0
        assert session.scalar(select(func.count()).select_from(ArtifactRow)) == 0
    assert jobs.job_get(db, claim.job.id).status == "running"
    assert not list(db.artifact_dir.glob("*.blob"))


def test_staging_does_not_hold_a_writer_transaction(context, monkeypatch):
    db, _, _ = context
    claim = claimed(context)
    jobs.mark_dispatched(db, claim)
    original = service.stage_run

    def verify_unlocked(*args):
        other = Database(Settings(data_dir=db.artifact_dir.parent, seed_examples=False))
        try:
            with other.engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA busy_timeout=1")
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                connection.rollback()
        finally:
            other.engine.dispose()
        return original(*args)

    monkeypatch.setattr(service, "stage_run", verify_unlocked)
    assert jobs.finish_job(db, claim, computed(context, claim)).status == "completed"


def test_failed_commit_leaves_no_run_idempotency_or_artifact(context):
    db, registry, body = context
    job = jobs.submit_job(db, registry, body, "run:legacy", legacy_key="legacy")
    claim = jobs.claim_job(db, "worker", job_id=job.id)
    jobs.mark_dispatched(db, claim)

    def fail_commit(*_):
        raise OSError("Injected queue publication commit failure")

    event.listen(db.session.class_, "before_commit", fail_commit)
    try:
        with pytest.raises(OSError, match="commit failure"):
            jobs.finish_job(db, claim, computed(context, claim))
    finally:
        event.remove(db.session.class_, "before_commit", fail_commit)
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(RunRow)) == 0
        assert session.get(IdempotencyRow, "legacy") is None
    assert jobs.job_get(db, claim.job.id).status == "running"
    assert not list(db.artifact_dir.glob("*.blob"))


def test_reserved_identity_and_legacy_run_key_are_published_together(context):
    db, registry, body = context
    job = jobs.submit_job(db, registry, body, "run:legacy", legacy_key="legacy")
    claim = jobs.claim_job(db, "worker", job_id=job.id)
    jobs.mark_dispatched(db, claim)
    result = computed(context, claim)
    finished = jobs.finish_job(db, claim, result)
    assert finished.run_id == claim.reserved_run_id
    with db.session() as session:
        run = service.run_get(session, finished.run_id)
        assert run.created_at == job.created_at
        assert run.scenario_snapshot == job.scenario_snapshot
        assert session.get(IdempotencyRow, "legacy").run_id == run.id
    with pytest.raises(jobs.LostLease):
        jobs.finish_job(db, claim, result)


def test_wrong_result_identity_is_rejected_before_staging(context):
    db, _, _ = context
    claim = claimed(context)
    jobs.mark_dispatched(db, claim)
    result = computed(context, claim)
    wrong = replace(result, run=result.run.model_copy(update={"id": "run_wrong"}))
    with pytest.raises(ValueError, match="identity"):
        jobs.finish_job(db, claim, wrong)


def test_known_preflight_failure_is_not_evidence_or_uncertain_external_ownership(context):
    db, _, _ = context
    claim = claimed(context, external=True)
    jobs.mark_dispatched(db, claim)
    failed = jobs.fail_job(db, claim, "Unsupported engine input", failure_http_status=422)
    assert failed.failure_http_status == 422 and not failed.outcome_uncertain
    assert jobs.list_resources(db)[0]["state"] == "idle"
    with db.session() as session:
        assert jobs.latest_job_evidence(session) == {}


def test_uncertain_external_failure_remains_gated(context):
    db, _, _ = context
    claim = claimed(context, external=True)
    jobs.mark_dispatched(db, claim)
    failed = jobs.fail_job(db, claim, "Lost remote response", outcome_uncertain=True)
    assert failed.status == "failed" and failed.outcome_uncertain
    assert jobs.list_resources(db)[0]["state"] == "reconcile_required"


def test_job_pagination_and_event_truncation_are_explicit(context):
    db, _, _ = context
    originals = [submit(context, f"job:{index}") for index in range(5)]
    first = jobs.job_list(db, limit=2)
    second = jobs.job_list(db, limit=2, before_seq=first["next_before_seq"])
    third = jobs.job_list(db, limit=2, before_seq=second["next_before_seq"])
    assert [item.id for page in (first, second, third) for item in page["items"]] == [job.id for job in reversed(originals)]
    assert third["next_before_seq"] is None
    with db.session.begin() as session:
        for i in range(210):
            session.add(JobEventRow(job_id=originals[0].id, at=service.utcnow(), type="test", message=str(i)))
    detail = jobs.job_get(db, originals[0].id)
    assert len(detail.events) == 200 and detail.events_total == 211


def test_worker_heartbeat_distinguishes_stopped_and_stale(context):
    db, _, _ = context
    jobs.touch_worker(db, "worker", "idle")
    assert jobs.list_workers(db)[0]["alive"]
    with db.session.begin() as session:
        session.get(WorkerRow, "worker").heartbeat_at = "2000-01-01T00:00:00.000000+00:00"
    assert not jobs.list_workers(db)[0]["alive"]
    jobs.touch_worker(db, "worker", "stopped")
    assert not jobs.list_workers(db)[0]["alive"]


@pytest.mark.parametrize("version", [1, 2])
def test_schema_migration_is_additive_and_preserves_existing_records(context, version):
    db, _, body = context
    with db.engine.begin() as connection:
        for model in (JobEventRow, EngineResourceRow, WorkerRow, JobRow):
            model.__table__.drop(connection)
        connection.execute(SchemaVersionRow.__table__.update().values(version=version))
    db.initialize()
    with db.session() as session:
        assert session.scalar(select(SchemaVersionRow.version)) == SCHEMA_VERSION
        assert service.scenario_get(session, body.scenario_id).name == "Durable queue fixture"
    assert "execution_jobs" in inspect(db.engine).get_table_names()


def _initialize_process(directory, barrier, results):
    db = Database(Settings(data_dir=Path(directory), seed_examples=False))
    try:
        barrier.wait(timeout=15)
        db.initialize()
        with db.session() as session:
            results.put(("ok", list(session.scalars(select(SchemaVersionRow.version)))))
    except BaseException as error:
        results.put(("error", repr(error)))
    finally:
        db.engine.dispose()


def test_fresh_database_initialization_is_safe_across_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(3), context.Queue()
    processes = [context.Process(target=_initialize_process, args=(str(tmp_path), barrier, results)) for _ in range(3)]
    for process in processes:
        process.start()
    try:
        outcomes = [results.get(timeout=25) for _ in processes]
        assert outcomes == [("ok", [SCHEMA_VERSION])] * 3
    finally:
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        results.close()
