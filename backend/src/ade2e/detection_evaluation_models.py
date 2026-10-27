"""Evaluation subject/result relationships; common jobs own lifecycle and leases."""

from sqlalchemy import JSON, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class DetectionEvaluationRow(Base):
    __tablename__ = "detection_evaluations"
    __table_args__ = (ForeignKeyConstraint(["dataset_id", "dataset_revision"], ["dataset_revisions.dataset_id", "dataset_revisions.revision"]),)
    job_id: Mapped[str] = mapped_column(ForeignKey("execution_jobs.id"), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(64))
    dataset_revision: Mapped[int] = mapped_column(Integer)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DetectionEvaluationSourceRow(Base):
    __tablename__ = "detection_evaluation_sources"
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("detection_evaluations.job_id"), primary_key=True)
    inference_job_id: Mapped[str] = mapped_column(ForeignKey("perception_jobs.job_id"), primary_key=True)
    manifest_hash: Mapped[str] = mapped_column(String(64))
