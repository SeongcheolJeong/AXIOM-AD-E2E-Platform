"""Immutable experiment manifests and fenced durable execution evidence."""
from sqlalchemy import ForeignKey, Integer, JSON, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class ExperimentRow(Base):
    __tablename__ = 'experiments'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('team_projects.id'), index=True)
    name: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(240), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))

class ExperimentExecutionRow(Base):
    __tablename__ = 'experiment_executions'
    job_id: Mapped[str] = mapped_column(ForeignKey('execution_jobs.id'), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey('experiments.id'), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('team_projects.id'), index=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fidelity: Mapped[str | None] = mapped_column(String(180), nullable=True)
    manifest: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

class ExperimentArtifactRow(Base):
    __tablename__ = 'experiment_artifacts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('experiment_executions.job_id'), index=True)
    name: Mapped[str] = mapped_column(String(180))
    media_type: Mapped[str] = mapped_column(String(180))
    storage_key: Mapped[str] = mapped_column(String(180), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
