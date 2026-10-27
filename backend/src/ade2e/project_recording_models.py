"""Explicit immutable permission to inspect one exact legacy recording source."""
from sqlalchemy import ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class ProjectRecordingGrantRow(Base):
    __tablename__ = 'project_recording_grants'
    __table_args__ = (UniqueConstraint('project_id', 'recording_id', name='uq_project_recording_source'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('team_projects.id'), index=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey('recordings.id'), index=True)
    recording_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    request_key: Mapped[str] = mapped_column(String(240), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))


class ProjectRecordingGrantRequestRow(Base):
    """Bind retries even when another key already granted the identical source."""
    __tablename__ = 'project_recording_grant_requests'
    request_key: Mapped[str] = mapped_column(String(240), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    grant_id: Mapped[str] = mapped_column(ForeignKey('project_recording_grants.id'), index=True)
