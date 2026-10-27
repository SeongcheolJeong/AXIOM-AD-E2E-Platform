"""Schema13 additive collection snapshots and atomic dataset publication receipts."""
from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class CollectionRow(Base):
    __tablename__ = 'data_collections'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), index=True)


class CollectionRevisionRow(Base):
    __tablename__ = 'data_collection_revisions'
    collection_id: Mapped[str] = mapped_column(ForeignKey('data_collections.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class CollectionDatasetRow(Base):
    __tablename__ = 'data_collection_datasets'
    __table_args__ = (
        ForeignKeyConstraint(['collection_id', 'collection_revision'],
                             ['data_collection_revisions.collection_id', 'data_collection_revisions.revision']),
        ForeignKeyConstraint(['dataset_id', 'dataset_revision'], ['dataset_revisions.dataset_id', 'dataset_revisions.revision']),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    collection_id: Mapped[str] = mapped_column(String(64), index=True)
    collection_revision: Mapped[int] = mapped_column(Integer)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_revision: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)
