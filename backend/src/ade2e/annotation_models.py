"""Schema14 additive label-assist intent and immutable publication evidence."""
from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AnnotationTaskRow(Base):
    __tablename__ = 'annotation_tasks'
    __table_args__ = (ForeignKeyConstraint(['dataset_id', 'dataset_revision'],
                        ['dataset_revisions.dataset_id', 'dataset_revisions.revision']),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class AnnotationPublicationRow(Base):
    __tablename__ = 'annotation_publications'
    __table_args__ = (ForeignKeyConstraint(['dataset_id', 'dataset_revision'],
                        ['dataset_revisions.dataset_id', 'dataset_revisions.revision']),)
    task_id: Mapped[str] = mapped_column(ForeignKey('annotation_tasks.id'), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_revision: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)
