"""Original driving-policy JSON input receipts, separate from detection datasets."""
from sqlalchemy import JSON, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class DrivingSequenceRow(Base):
    __tablename__='driving_sequences'
    id: Mapped[str]=mapped_column(String(64),primary_key=True)
    project_id: Mapped[str]=mapped_column(String(64),index=True)
    created_at: Mapped[str]=mapped_column(String(40),index=True)
    manifest_sha256: Mapped[str]=mapped_column(String(64),index=True)
    input_hash: Mapped[str]=mapped_column(String(64),index=True)
    manifest: Mapped[bytes]=mapped_column(LargeBinary)
    payload: Mapped[dict]=mapped_column(JSON)

