"""Immutable project-scoped native map, asset and controller catalog revisions."""
from sqlalchemy import Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class SimulationCatalogRevisionRow(Base):
    __tablename__ = 'simulation_catalog_revisions'
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
