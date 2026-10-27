"""Immutable suite revisions, atomic campaign membership and sealed reports."""

from sqlalchemy import JSON, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class SuiteRow(Base):
    __tablename__ = "test_suites"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class SuiteRevisionRow(Base):
    __tablename__ = "test_suite_revisions"
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)


class CampaignRow(Base):
    __tablename__ = "test_campaigns"
    __table_args__ = (ForeignKeyConstraint(["suite_id", "suite_revision"], ["test_suite_revisions.suite_id", "test_suite_revisions.revision"]),
                      {"sqlite_autoincrement": True})
    submission_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    suite_id: Mapped[str] = mapped_column(String(64), index=True)
    suite_revision: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[str] = mapped_column(String(40))
    suite_snapshot: Mapped[dict] = mapped_column(JSON)
    adapter_snapshot: Mapped[dict] = mapped_column(JSON)
    execution_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CampaignMemberRow(Base):
    __tablename__ = "test_campaign_members"
    campaign_id: Mapped[str] = mapped_column(ForeignKey("test_campaigns.id"), primary_key=True)
    case_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("execution_jobs.id"), unique=True)


class BaselineRow(Base):
    __tablename__ = "test_baselines"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    campaign_id: Mapped[str] = mapped_column(ForeignKey("test_campaigns.id"), index=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    summary: Mapped[dict] = mapped_column(JSON)
