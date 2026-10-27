"""MCAP catalog and seek index; message bytes live in managed sidecar files."""

from sqlalchemy import JSON, ForeignKey, ForeignKeyConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class RecordingRow(Base):
    __tablename__ = "recordings"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    import_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    index_storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)


class RecordingSchemaRow(Base):
    __tablename__ = "recording_schemas"
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), primary_key=True)
    schema_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class RecordingChannelRow(Base):
    __tablename__ = "recording_channels"
    __table_args__ = (ForeignKeyConstraint(["recording_id", "schema_ref"], ["recording_schemas.recording_id", "recording_schemas.schema_id"]),)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), primary_key=True)
    channel_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # MCAP schema_id=0 denotes no schema and has no schema foreign key.
    schema_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)


class RecordingMessageRow(Base):
    __tablename__ = "recording_messages"
    __table_args__ = (
        ForeignKeyConstraint(["recording_id", "channel_id"], ["recording_channels.recording_id", "recording_channels.channel_id"]),
        Index("ix_recording_seek", "recording_id", "channel_id", "log_time_key", "ordinal"),
    )
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(Integer)
    log_time_key: Mapped[str] = mapped_column(String(20))
    publish_time_key: Mapped[str] = mapped_column(String(20))
    sequence: Mapped[int] = mapped_column(Integer)
    payload_offset: Mapped[int] = mapped_column(Integer)
    payload_length: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
