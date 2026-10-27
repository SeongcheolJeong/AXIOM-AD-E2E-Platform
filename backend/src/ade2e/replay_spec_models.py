"""Pinned replay execution intent; results belong to the common Experiment queue."""
from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ReplaySpecRow(Base):
    __tablename__ = 'replay_specs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence_id: Mapped[str] = mapped_column(ForeignKey('scene_sequences.id'), index=True)
    project_id: Mapped[str] = mapped_column(String(180), index=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)

