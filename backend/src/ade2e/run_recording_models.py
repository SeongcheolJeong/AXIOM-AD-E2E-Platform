"""Relational provenance from an engine artifact to an imported recording."""
from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class RecordingOriginRow(Base):
    __tablename__ = "recording_origins"
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), unique=True)
    created_at: Mapped[str] = mapped_column(String(40))
    definition_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
