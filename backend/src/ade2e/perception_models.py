"""Typed subjects and independently committed frames for the common queue."""

from sqlalchemy import JSON, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class PerceptionJobRow(Base):
    __tablename__ = "perception_jobs"
    __table_args__ = (
        ForeignKeyConstraint(["model_id", "model_revision"], ["perception_model_revisions.model_id", "perception_model_revisions.revision"]),
        ForeignKeyConstraint(["recording_id", "channel_id"], ["recording_channels.recording_id", "recording_channels.channel_id"]),
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("execution_jobs.id"), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(64))
    model_revision: Mapped[int] = mapped_column(Integer)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"))
    channel_id: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PerceptionFrameRow(Base):
    __tablename__ = "perception_frames"
    __table_args__ = (ForeignKeyConstraint(["recording_id", "ordinal"], ["recording_messages.recording_id", "recording_messages.ordinal"]),)
    job_id: Mapped[str] = mapped_column(ForeignKey("perception_jobs.job_id"), primary_key=True)
    selection_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    image_storage_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)


class PerceptionComparisonRow(Base):
    __tablename__ = "perception_comparisons"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    left_job_id: Mapped[str] = mapped_column(ForeignKey("perception_jobs.job_id"))
    right_job_id: Mapped[str] = mapped_column(ForeignKey("perception_jobs.job_id"))
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)
