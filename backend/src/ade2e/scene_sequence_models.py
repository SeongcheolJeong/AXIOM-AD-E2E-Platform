"""Immutable temporal input references for driving-policy and planning jobs."""
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class SceneSequenceRow(Base):
    __tablename__ = 'scene_sequences'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(180), index=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)

