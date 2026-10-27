"""Remote attempt binding and read-only retrieval subjects on the common queue."""
from sqlalchemy import JSON, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class RemoteAttemptRow(Base):
    __tablename__ = 'remote_execution_attempts'
    source_job_id: Mapped[str] = mapped_column(ForeignKey('execution_jobs.id'), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(180), unique=True)
    store_id: Mapped[str] = mapped_column(String(180))
    request_hash: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))
    recovered_run_id: Mapped[str | None] = mapped_column(ForeignKey('runs.id'), nullable=True)
    accepted_archive_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RecoveryRow(Base):
    __tablename__ = 'execution_recoveries'
    job_id: Mapped[str] = mapped_column(ForeignKey('execution_jobs.id'), primary_key=True)
    source_job_id: Mapped[str] = mapped_column(ForeignKey('remote_execution_attempts.source_job_id'), index=True)
    source_run_id: Mapped[str | None] = mapped_column(ForeignKey('runs.id'), nullable=True)
    source_execution_id: Mapped[str] = mapped_column(String(180))
    outcome: Mapped[str] = mapped_column(String(24), default='pending')
    run_id: Mapped[str | None] = mapped_column(ForeignKey('runs.id'), nullable=True)
    remote_status: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    archive_storage_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    archive_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    archive_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manifest: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
