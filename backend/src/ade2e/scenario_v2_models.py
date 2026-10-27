"""Additive scenario v2 storage, independent of legacy scenario tables/hashes."""
from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base

class ScenarioV2Row(Base):
    __tablename__='scenario_v2'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),index=True)
    revision: Mapped[int]=mapped_column(Integer)

class ScenarioV2RevisionRow(Base):
    __tablename__='scenario_v2_revisions'
    scenario_id: Mapped[str]=mapped_column(ForeignKey('scenario_v2.id'),primary_key=True)
    revision: Mapped[int]=mapped_column(Integer,primary_key=True)
    request_key: Mapped[str]=mapped_column(String(240),unique=True)
    request_hash: Mapped[str]=mapped_column(String(64))
    manifest: Mapped[bytes]=mapped_column(LargeBinary)
    manifest_hash: Mapped[str]=mapped_column(String(64))
