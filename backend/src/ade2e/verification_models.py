"""Additive immutable plan revisions and durable execution receipts."""
from sqlalchemy import ForeignKey, Integer, JSON, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class VerificationPlanRow(Base):
    __tablename__ = 'verification_plans'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)

class VerificationRevisionRow(Base):
    __tablename__ = 'verification_plan_revisions'
    plan_id: Mapped[str] = mapped_column(ForeignKey('verification_plans.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(240), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))

class VerificationAttemptRow(Base):
    __tablename__ = 'verification_attempts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey('verification_plans.id'), index=True)
    request_key: Mapped[str] = mapped_column(String(240), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
