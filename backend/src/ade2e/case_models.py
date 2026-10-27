"""Additive schema12: immutable source segments and investigation revisions."""
from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class SegmentRow(Base):
    __tablename__ = 'investigation_segments'
    __table_args__ = (ForeignKeyConstraint(['recording_id', 'channel_id'],
        ['recording_channels.recording_id', 'recording_channels.channel_id']),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey('recordings.id'), index=True)
    channel_id: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class CaseRow(Base):
    __tablename__ = 'investigation_cases'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    segment_id: Mapped[str] = mapped_column(ForeignKey('investigation_segments.id'))
    title: Mapped[str] = mapped_column(String(180))
    state: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    creation_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))


class CaseRevisionRow(Base):
    __tablename__ = 'investigation_case_revisions'
    case_id: Mapped[str] = mapped_column(ForeignKey('investigation_cases.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class CaseEvaluationRow(Base):
    __tablename__ = 'investigation_case_evaluations'
    __table_args__ = (ForeignKeyConstraint(['case_id', 'revision'],
        ['investigation_case_revisions.case_id', 'investigation_case_revisions.revision']),)
    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey('detection_evaluations.job_id'), primary_key=True)
    image_id: Mapped[str] = mapped_column(ForeignKey('dataset_images.id'), primary_key=True)
