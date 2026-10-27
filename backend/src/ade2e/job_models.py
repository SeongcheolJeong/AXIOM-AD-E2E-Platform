"""Durable execution intent, append-only lifecycle events and engine ownership."""

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class JobRow(Base):
    __tablename__ = "execution_jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('simulation', 'perception', 'evaluation', 'stack_replay', 'recovery', 'experiment')", name="ck_job_kind"),
        CheckConstraint("(kind = 'simulation' AND scenario_id IS NOT NULL AND scenario_name IS NOT NULL AND scenario_revision IS NOT NULL) OR (kind IN ('perception', 'evaluation', 'stack_replay', 'recovery', 'experiment') AND scenario_id IS NULL AND scenario_name IS NULL AND scenario_revision IS NULL AND run_id IS NULL)", name="ck_job_subject"),
        Index("ix_job_queue", "status", "submission_seq"),
        Index("ix_job_evidence", "scenario_id", "submission_seq"),
        {"sqlite_autoincrement": True},
    )
    submission_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(240), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict] = mapped_column(JSON)
    prepared: Mapped[dict] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24), default="simulation", server_default="simulation")
    scenario_id: Mapped[str | None] = mapped_column(ForeignKey("scenarios.id"), nullable=True)
    scenario_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    scenario_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adapter_id: Mapped[str] = mapped_column(String(180))
    adapter_version: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dispatched_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    reserved_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    legacy_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    owner_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_requested_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancellation_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    resource_required: Mapped[bool] = mapped_column(Boolean, default=True)
    resource_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    outcome_uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)


class JobEventRow(Base):
    __tablename__ = "execution_job_events"
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("execution_jobs.id"), index=True)
    at: Mapped[str] = mapped_column(String(40))
    type: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(String(2000))


class EngineResourceRow(Base):
    __tablename__ = "execution_resources"
    adapter_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    state: Mapped[str] = mapped_column(String(24), default="idle")
    job_id: Mapped[str | None] = mapped_column(ForeignKey("execution_jobs.id"), nullable=True)
    owner_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(String(2000), default="")


class WorkerRow(Base):
    __tablename__ = "execution_workers"
    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    heartbeat_at: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(24))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("execution_jobs.id"), nullable=True)
