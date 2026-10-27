"""Immutable visual indexes, revisioned human declarations and selection receipts."""
from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class SceneIndexRow(Base):
    __tablename__ = 'scene_indexes'
    __table_args__ = (ForeignKeyConstraint(['collection_id', 'collection_revision'],
        ['data_collection_revisions.collection_id', 'data_collection_revisions.revision']),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    collection_id: Mapped[str] = mapped_column(String(64), index=True)
    collection_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class SceneReviewRow(Base):
    __tablename__ = 'scene_tag_reviews'
    index_id: Mapped[str] = mapped_column(ForeignKey('scene_indexes.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class SceneSelectionRow(Base):
    __tablename__ = 'scene_collection_selections'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    index_id: Mapped[str] = mapped_column(ForeignKey('scene_indexes.id'), index=True)
    collection_id: Mapped[str] = mapped_column(ForeignKey('data_collections.id'), index=True)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)
