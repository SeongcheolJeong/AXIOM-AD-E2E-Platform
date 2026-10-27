"""Original image capture and immutable dataset/annotation revision relations."""
from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class DatasetImageRow(Base):
    __tablename__ = 'dataset_images'
    __table_args__ = (ForeignKeyConstraint(['recording_id','ordinal'], ['recording_messages.recording_id','recording_messages.ordinal']),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recording_id: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[str] = mapped_column(String(40))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class DatasetRow(Base):
    __tablename__ = 'datasets'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), index=True)


class DatasetRevisionRow(Base):
    __tablename__ = 'dataset_revisions'
    dataset_id: Mapped[str] = mapped_column(ForeignKey('datasets.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class DatasetMembershipRow(Base):
    __tablename__ = 'dataset_memberships'
    __table_args__ = (ForeignKeyConstraint(['dataset_id','revision'], ['dataset_revisions.dataset_id','dataset_revisions.revision']),)
    dataset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[str] = mapped_column(ForeignKey('dataset_images.id'), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
