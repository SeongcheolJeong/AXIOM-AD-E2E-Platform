"""Single-host SQLite execution queue: durable intent, leases and fenced results.

No database transaction spans engine execution or artifact staging. Expired
attempts are never automatically retried; DB fencing cannot stop a remote engine.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, tuple_

from .adapters import AdapterOwnershipLost, Registry, supports_cancellation
from .database import Database, IdempotencyRow
from .job_models import EngineResourceRow, JobEventRow, JobRow, WorkerRow
from .job_schemas import JobDetail, JobStatus, JobSummary
from .reference import ReferenceAdapter
from .schemas import RunCreate
from . import service
from .service import ComputedRun, PreparedRun, digest, prepare_run, run_request_hash, uid, utcnow

ACTIVE = ("running", "cancel_requested")
TERMINAL = ("completed", "failed", "cancelled", "interrupted")
EVENT_LIMIT = 200
WORKER_ALIVE_SECONDS = 60


class LostLease(AdapterOwnershipLost):
    """This worker must not publish or turn lost ownership into cancellation."""


@dataclass(frozen=True)
class Claim:
    job: Any
    token: str
    prepared: Any
    reserved_run_id: str
    kind: str = "simulation"


@contextmanager
def _write(db: Database):
    if db.engine.dialect.name != "sqlite":
        raise HTTPException(503, "Durable execution currently supports a single-host SQLite database only")
    with db.session() as session:
        try:
            # Acquire before any read: SELECT followed by a deferred upgrade
            # cannot arbitrate competing processes safely.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def _row(session, job_id: str) -> JobRow:
    row = session.scalar(select(JobRow).where(JobRow.id == job_id))
    if row is None:
        raise HTTPException(404, "Execution job not found")
    return row


def _event(session, row: JobRow, event_type: str, message: str, now: str):
    session.add(JobEventRow(job_id=row.id, at=now, type=event_type, message=str(message)[:2000]))


def _summary(row: JobRow) -> JobSummary:
    if row.kind != "simulation":
        raise HTTPException(404, "Simulation job not found")
    return JobSummary.model_validate({name: getattr(row, name) for name in JobSummary.model_fields})


def _detail(session, row: JobRow) -> JobDetail:
    if row.kind == 'experiment':
        from .experiment_jobs import detail
        return detail(session, row)
    if row.kind == 'recovery':
        from .recoveries import detail
        return detail(session, row)
    if row.kind == "perception":
        from .perception_jobs import detail
        return detail(session, row)
    if row.kind == "evaluation":
        from .detection_evaluations import detail
        return detail(session, row)
    if row.kind == "stack_replay":
        from .stack_replays import detail
        return detail(session, row)
    session.flush()
    events = list(session.scalars(select(JobEventRow).where(JobEventRow.job_id == row.id).order_by(JobEventRow.sequence.desc()).limit(EVENT_LIMIT)))
    total = session.scalar(select(func.count()).select_from(JobEventRow).where(JobEventRow.job_id == row.id))
    return JobDetail(**_summary(row).model_dump(), request=row.request, scenario_snapshot=row.prepared["scenario"],
                     execution_context=PreparedRun.from_payload(row.prepared).execution_context,
                     events=[{"at": event.at, "type": event.type, "message": event.message} for event in reversed(events)],
                     events_total=total, failure_http_status=row.failure_http_status)


def _lease_end(now: str, seconds: float) -> str:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError("lease_seconds must be greater than zero and at most 300")
    return (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def _owns(row: JobRow, claim: Claim, now: str) -> bool:
    return (row.status in ACTIVE and row.owner_token == claim.token and row.worker_id == claim.job.worker_id
            and row.lease_expires_at is not None and row.lease_expires_at > now)


def _live(session, claim: Claim) -> JobRow:
    row = _row(session, claim.job.id)
    if not _owns(row, claim, utcnow()):
        raise LostLease("Execution lease expired or this worker no longer owns publication")
    return row


def _release_resource(session, row: JobRow, now: str, *, uncertain: bool):
    if not row.resource_required:
        return
    resource = session.get(EngineResourceRow, row.resource_key or row.adapter_id)
    if resource is None or resource.job_id != row.id or resource.owner_token != row.owner_token:
        raise LostLease("Engine resource ownership no longer matches this execution")
    resource.updated_at = now
    if uncertain:
        resource.state = "reconcile_required"
        resource.note = "The engine may still be active. Stop the old dispatcher and confirm remote cleanup before operator reconciliation."
    else:
        resource.state, resource.job_id, resource.owner_token = "idle", None, None
        resource.note = "Previous execution ended without an uncertain external outcome."


def _terminal(session, row: JobRow, status: str, now: str, message: str, *, uncertain: bool = False):
    _release_resource(session, row, now, uncertain=uncertain)
    row.status, row.updated_at, row.finished_at = status, now, now
    row.outcome_uncertain = uncertain
    row.owner_token, row.lease_expires_at = None, None
    _event(session, row, status, message, now)
    if row.kind == "perception":
        from .perception_jobs import seal_terminal
        seal_terminal(session, row)
    if row.kind == "evaluation":
        from .detection_evaluations import seal_terminal
        seal_terminal(session, row)
    if row.kind == "stack_replay":
        from .stack_replays import seal_terminal
        seal_terminal(session, row)
    if row.kind == 'recovery':
        from .recoveries import seal_terminal
        seal_terminal(session, row)
    if row.kind == 'experiment':
        from .experiment_jobs import seal_terminal
        seal_terminal(session, row)


def _expire(session, now: str) -> int:
    rows = list(session.scalars(select(JobRow).where(JobRow.status.in_(ACTIVE), JobRow.lease_expires_at <= now)))
    for row in rows:
        row.error = "Worker lease expired; engine outcome is uncertain. This job will not be automatically retried."
        _terminal(session, row, "interrupted", now, row.error, uncertain=True)
    return len(rows)


def submit_job(db: Database, registry: Registry, body: RunCreate, key: str, *, legacy_key: str | None = None) -> JobDetail:
    if not isinstance(key, str) or not key or len(key) > 240:
        raise HTTPException(422, "A nonempty job idempotency key of at most 240 characters is required")
    request_hash = run_request_hash(body)
    with _write(db) as session:
        existing = session.scalar(select(JobRow).where(JobRow.idempotency_key == key))
        if existing is not None:
            if existing.kind != "simulation" or existing.request_hash != request_hash or existing.legacy_key != legacy_key:
                raise HTTPException(409, "Idempotency-Key was already used for a different execution request")
            return _detail(session, existing)
        if legacy_key and session.get(IdempotencyRow, legacy_key) is not None:
            raise HTTPException(409, "Legacy idempotency key already identifies a run; retrieve it through the legacy runs endpoint")
        prepared = prepare_run(session, registry, body)
        row = insert_prepared_job(session, registry, body, prepared, key, legacy_key=legacy_key)
        return _detail(session, row)


def insert_prepared_job(session, registry: Registry, body: RunCreate, prepared: PreparedRun, key: str,
                        *, legacy_key: str | None = None) -> JobRow:
    """Insert validated, pinned work in the caller's transaction, without commit.

    Campaigns use this to publish all jobs plus membership atomically. The
    caller owns request idempotency; every internal job key remains unique.
    """
    scenario, metadata = prepared.scenario, prepared.adapter_metadata
    request_hash = run_request_hash(body)
    if request_hash != prepared.request_hash or body.scenario_id != scenario.id or body.adapter_id != metadata.id:
        raise ValueError("Prepared job does not match its request")
    adapter = registry.get(metadata.id)
    if adapter is None:
        raise HTTPException(422, "Prepared adapter is not installed")
    now = utcnow()
    row = JobRow(id=uid("job"), idempotency_key=key, request_hash=request_hash, request=body.model_dump(mode="json", exclude_none=True),
                 prepared=prepared.to_payload(), input_hash=prepared.input_hash,
                 scenario_id=scenario.id, scenario_name=scenario.name, scenario_revision=scenario.revision,
                 adapter_id=metadata.id, adapter_version=metadata.version, status="queued", created_at=now, updated_at=now,
                 legacy_key=legacy_key, cancellation_supported=prepared.execution_context is None and supports_cancellation(adapter),
                 resource_required=metadata.id == 'carla' or type(adapter) is not ReferenceAdapter, outcome_uncertain=False, evidence_eligible=False)
    session.add(row)
    session.flush()
    _event(session, row, "queued", "Input snapshot and adapter version saved. No engine has been dispatched.", now)
    return row


def job_get(db: Database, job_id: str, *, kind: str = "simulation") -> JobDetail:
    with db.read_session() as session:
        row = _row(session, job_id)
        if row.kind != kind:
            raise HTTPException(404, "Job not found in the requested domain")
        return _detail(session, row)


def job_list(db: Database, status: JobStatus | None = None, limit: int = 100, before_seq: int | None = None) -> dict:
    if not 1 <= limit <= 100 or (before_seq is not None and before_seq < 1):
        raise HTTPException(422, "Job page limit must be 1..100 and before_seq must be positive")
    if status is not None and status not in ("queued", *ACTIVE, *TERMINAL):
        raise HTTPException(422, "Unknown execution job status")
    with db.session() as session:
        query = select(JobRow).where(JobRow.kind == "simulation").order_by(JobRow.submission_seq.desc())
        if status is not None:
            query = query.where(JobRow.status == status)
        if before_seq is not None:
            query = query.where(JobRow.submission_seq < before_seq)
        rows = list(session.scalars(query.limit(limit + 1)))
        return {"items": [_summary(row) for row in rows[:limit]],
                "next_before_seq": rows[limit - 1].submission_seq if len(rows) > limit else None}


def claim_job(db: Database, worker_id: str, lease_seconds: float = 30, job_id: str | None = None,
              *, capabilities: set[tuple[str, str, str]] | None = None) -> Claim | None:
    if not worker_id or len(worker_id) > 180:
        raise ValueError("worker_id must contain 1..180 characters")
    now = utcnow()
    expiry = _lease_end(now, lease_seconds)
    with _write(db) as session:
        now, expiry = utcnow(), _lease_end(utcnow(), lease_seconds)
        _expire(session, now)
        query = select(JobRow).where(JobRow.status == "queued").order_by(JobRow.submission_seq)
        query = query.where(JobRow.kind == "simulation") if capabilities is None else query.where(tuple_(JobRow.kind, JobRow.adapter_id, JobRow.adapter_version).in_(capabilities))
        if job_id is not None:
            query = query.where(JobRow.id == job_id)
        for row in session.scalars(query):
            from .object_beta_access import can_claim_experiment
            if not can_claim_experiment(session, row):
                continue
            resource = None
            if row.resource_required:
                resource = session.get(EngineResourceRow, row.resource_key or row.adapter_id)
                if resource is not None and resource.state != "idle":
                    continue
            token = uid("lease")
            if row.resource_required:
                if resource is None:
                    resource = EngineResourceRow(adapter_id=row.resource_key or row.adapter_id, updated_at=now)
                    session.add(resource)
                resource.state, resource.job_id, resource.owner_token = "reserved", row.id, token
                resource.updated_at, resource.note = now, "Exclusive engine dispatch reserved by a live worker."
            row.status, row.worker_id, row.owner_token = "running", worker_id, token
            row.started_at, row.updated_at, row.lease_expires_at = now, now, expiry
            row.reserved_run_id = uid("run") if row.kind == "simulation" else None
            _event(session, row, "claimed", "Worker claimed this job; engine dispatch has not yet been recorded.", now)
            if row.kind == 'recovery':
                from .recoveries import PreparedRecovery
                prepared = PreparedRecovery.from_payload(row.prepared)
            elif row.kind == "perception":
                from .perception_jobs import PreparedPerception
                prepared = PreparedPerception.from_payload(row.prepared)
            elif row.kind == "evaluation":
                from .detection_evaluations import PreparedEvaluation
                prepared = PreparedEvaluation.from_payload(row.prepared)
            elif row.kind == "stack_replay":
                from .stack_replays import PreparedStackReplay
                prepared = PreparedStackReplay.from_payload(row.prepared)
            elif row.kind == 'experiment':
                from .experiment_jobs import PreparedExperiment
                prepared = PreparedExperiment.from_payload(row.prepared)
            else:
                prepared = PreparedRun.from_payload(row.prepared)
            return Claim(_detail(session, row), token, prepared, row.reserved_run_id, row.kind)
    return None


def mark_dispatched(db: Database, claim: Claim) -> bool:
    with _write(db) as session:
        row = _live(session, claim)
        if row.dispatched_at is not None:
            raise HTTPException(409, "This claim has already been dispatched; never execute it twice")
        now = utcnow()
        if row.cancel_requested_at is not None:
            _terminal(session, row, "cancelled", now, "Cancellation acknowledged before engine dispatch.")
            return False
        row.dispatched_at, row.updated_at, row.evidence_eligible = now, now, row.kind == "simulation"
        _event(session, row, "dispatched", "Dispatch intent committed. A subsequent worker loss may leave an uncertain engine outcome.", now)
        return True


def heartbeat_job(db: Database, claim: Claim, lease_seconds: float = 30) -> bool:
    _lease_end(utcnow(), lease_seconds)
    with _write(db) as session:
        row = _row(session, claim.job.id)
        now = utcnow()
        if not _owns(row, claim, now):
            return False
        row.lease_expires_at, row.updated_at = _lease_end(now, lease_seconds), now
        worker = session.get(WorkerRow, row.worker_id)
        if worker is not None and worker.state == "running" and worker.job_id == row.id:
            worker.heartbeat_at = now
        return True


def cancellation_requested(db: Database, claim: Claim) -> bool:
    with db.session() as session:
        return _live(session, claim).cancel_requested_at is not None


def cancel_job(db: Database, job_id: str, *, kind: str = "simulation") -> JobDetail:
    with _write(db) as session:
        _expire(session, utcnow())
        row = _row(session, job_id)
        if row.kind != kind:
            raise HTTPException(404, "Job not found in the requested domain")
        cancel_job_row(session, row)
        return _detail(session, row)


def cancel_job_row(session, row: JobRow, *, now: str | None = None) -> None:
    """Request cancellation inside the caller's atomic transaction."""
    now = now or utcnow()
    if row.status == "queued":
        row.cancel_requested_at = now
        row.status, row.updated_at, row.finished_at = "cancelled", now, now
        _event(session, row, "cancelled", "Queued job cancelled without engine dispatch.", now)
        if row.kind == "perception":
            from .perception_jobs import seal_terminal
            seal_terminal(session, row)
        if row.kind == "evaluation":
            from .detection_evaluations import seal_terminal
            seal_terminal(session, row)
        if row.kind == "stack_replay":
            from .stack_replays import seal_terminal
            seal_terminal(session, row)
        if row.kind == 'recovery':
            from .recoveries import seal_terminal
            seal_terminal(session, row)
        if row.kind == 'experiment':
            from .experiment_jobs import seal_terminal
            seal_terminal(session, row)
    elif row.status in ACTIVE and row.cancel_requested_at is None:
        row.cancel_requested_at, row.updated_at, row.status = now, now, "cancel_requested"
        message = ("Cancellation requested; awaiting the adapter's acknowledgement." if row.cancellation_supported else
                   "Cancellation requested. This adapter cannot stop a dispatched engine; a complete result will be retained if it arrives.")
        _event(session, row, "cancel_requested", message, now)


def finish_job(db: Database, claim: Claim, computed: ComputedRun) -> JobDetail:
    with db.session() as session:
        row = _live(session, claim)
        if row.dispatched_at is None:
            raise HTTPException(409, "A result cannot be published before dispatch")
        if (computed.run.id != row.reserved_run_id or computed.run.created_at != row.created_at
                or computed.run.provenance.input_hash != row.input_hash or claim.prepared.input_hash != row.input_hash):
            raise ValueError("Computed result identity differs from this job's reserved execution")
    # Staging is outside the writer transaction, so other workers can renew.
    staged = service.stage_run(db, claim.prepared, computed, claim.token)
    try:
        with _write(db) as session:
            row = _live(session, claim)
            run = service.publish_run(session, db, claim.prepared, computed, row.legacy_key, staged=staged)
            _live(session, claim)  # Publication can fail or outlive its lease.
            if run.id != row.reserved_run_id:
                raise ValueError("Published run does not match this job's reserved execution")
            now = utcnow()
            row.run_id, row.error = run.id, run.error
            if row.cancel_requested_at is not None:
                _event(session, row, "cancel_not_honoured", "A complete execution result arrived without a cancellation acknowledgement; its evidence has been retained.", now)
            status = "completed" if run.status == "completed" else "failed"
            uncertain = row.resource_required and run.status == "error"
            _terminal(session, row, status, now, "Run and artifact references published atomically with the job outcome.", uncertain=uncertain)
            return _detail(session, row)
    finally:
        service.cleanup_staged_run(db, staged)


def fail_job(db: Database, claim: Claim, error, *, status: str = "failed", outcome_uncertain: bool = False,
             failure_http_status: int | None = None, before_dispatch: bool = False) -> JobDetail:
    if status not in ("failed", "cancelled", "interrupted"):
        raise ValueError("Failure status must be failed, cancelled or interrupted")
    with _write(db) as session:
        row = _live(session, claim)
        if status == "cancelled" and row.cancel_requested_at is None:
            raise HTTPException(409, "Cancellation acknowledgement has no corresponding cancellation request")
        if status == "cancelled" and outcome_uncertain:
            raise ValueError("An uncertain outcome cannot be acknowledged as cancelled")
        row.error = str(error)[:2000]
        row.failure_http_status = failure_http_status
        if row.kind == 'experiment':
            from .experiment_jobs import record_failure_diagnostics
            record_failure_diagnostics(db, session, row)
        if failure_http_status == 422 or (status == "cancelled" and before_dispatch):
            row.evidence_eligible = False
        _terminal(session, row, status, utcnow(), row.error, uncertain=outcome_uncertain)
        return _detail(session, row)


def expire_jobs(db: Database) -> int:
    with _write(db) as session:
        return _expire(session, utcnow())


def latest_job_evidence(session) -> dict[str, JobRow]:
    latest = {}
    rows = session.scalars(select(JobRow).where(JobRow.kind == "simulation", JobRow.dispatched_at.is_not(None), JobRow.evidence_eligible.is_(True)).order_by(JobRow.submission_seq.desc()))
    for row in rows:
        latest.setdefault(row.scenario_id, row)
    return latest


def touch_worker(db: Database, worker_id: str, state: str, job_id: str | None = None):
    if not worker_id or len(worker_id) > 180 or state not in ("idle", "running", "stopped"):
        raise ValueError("Invalid worker identity or state")
    if (state == "running") != (job_id is not None):
        raise ValueError("Only running workers must identify their current job")
    with _write(db) as session:
        if job_id is not None:
            _row(session, job_id)
        row = session.get(WorkerRow, worker_id)
        if row is None:
            row = WorkerRow(id=worker_id)
            session.add(row)
        row.heartbeat_at, row.state, row.job_id = utcnow(), state, job_id


def list_workers(db: Database) -> list[dict]:
    now = datetime.now(timezone.utc)
    with db.session() as session:
        return [{"id": row.id, "heartbeat_at": row.heartbeat_at, "state": row.state, "job_id": row.job_id,
                 "alive": row.state != "stopped" and (now - datetime.fromisoformat(row.heartbeat_at)).total_seconds() <= WORKER_ALIVE_SECONDS}
                for row in session.scalars(select(WorkerRow).order_by(WorkerRow.heartbeat_at.desc(), WorkerRow.id))]


def list_resources(db: Database) -> list[dict]:
    with db.session() as session:
        return [{"adapter_id": row.adapter_id, "state": row.state, "job_id": row.job_id, "updated_at": row.updated_at, "note": row.note}
                for row in session.scalars(select(EngineResourceRow).order_by(EngineResourceRow.adapter_id))]


def reconcile_resource(db: Database, adapter_id: str, job_id: str, note: str, confirmed_stopped: bool = True) -> dict:
    if confirmed_stopped is not True or not isinstance(note, str) or not note.strip() or len(note) > 1000:
        raise HTTPException(422, "Confirm the old dispatcher and remote engine are stopped, and provide a reconciliation note")
    with _write(db) as session:
        resource = session.get(EngineResourceRow, adapter_id)
        if resource is None or resource.state != "reconcile_required" or resource.job_id != job_id:
            raise HTTPException(409, "The resource is not awaiting reconciliation for this exact job")
        row = _row(session, job_id)
        now = utcnow()
        resource.state, resource.job_id, resource.owner_token = "idle", None, None
        resource.updated_at, resource.note = now, note.strip()
        if row.kind != 'experiment':
            _event(session, row, "resource_reconciled", "Operator declared dispatcher stopped and engine cleanup complete: " + note.strip() + ". The previous job's uncertain outcome is unchanged.", now)
            row.updated_at = now
        # Experiment terminal manifests are immutable. Operational cleanup is
        # recorded on the resource; it cannot alter the sealed execution or
        # retroactively remove its uncertain outcome.
        return {"adapter_id": resource.adapter_id, "state": resource.state, "job_id": None, "updated_at": now, "note": resource.note}
