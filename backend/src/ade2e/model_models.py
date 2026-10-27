"""Model bytes and immutable declarations with explicit artifact relationships."""

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ModelArtifactRow(Base):
    __tablename__ = "perception_model_artifacts"
    __table_args__ = (UniqueConstraint("name", "sha256", "size_bytes"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)


class ModelRow(Base):
    __tablename__ = "perception_models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), index=True)


class ModelRevisionRow(Base):
    __tablename__ = "perception_model_revisions"

    model_id: Mapped[str] = mapped_column(ForeignKey("perception_models.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("perception_model_artifacts.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)
