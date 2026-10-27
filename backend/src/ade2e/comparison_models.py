"""Immutable canonical comparison bytes and searchable captured metadata."""

from sqlalchemy import JSON, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ComparisonEvidenceRow(Base):
    __tablename__ = "test_comparisons"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    campaign_id: Mapped[str] = mapped_column(ForeignKey("test_campaigns.id"), index=True)
    baseline_id: Mapped[str] = mapped_column(ForeignKey("test_baselines.id"), index=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("test_suites.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict] = mapped_column(JSON)
