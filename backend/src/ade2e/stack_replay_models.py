"""Stack replay subjects and independently published ticks on the common queue."""
from sqlalchemy import JSON, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class StackReplayRow(Base):
    __tablename__ = 'stack_replays'
    __table_args__ = (
        ForeignKeyConstraint(['stack_id', 'stack_revision'], ['component_stack_revisions.stack_id', 'component_stack_revisions.revision']),
        ForeignKeyConstraint(['recording_id', 'binding_revision'], ['recording_sensor_binding_revisions.recording_id', 'recording_sensor_binding_revisions.revision']),
        ForeignKeyConstraint(['recording_id', 'lidar_channel_id'], ['recording_channels.recording_id', 'recording_channels.channel_id']),
        ForeignKeyConstraint(['recording_id', 'ego_channel_id'], ['recording_channels.recording_id', 'recording_channels.channel_id']),
    )
    job_id: Mapped[str] = mapped_column(ForeignKey('execution_jobs.id'), primary_key=True)
    stack_id: Mapped[str] = mapped_column(String(64))
    stack_revision: Mapped[int] = mapped_column(Integer)
    recording_id: Mapped[str] = mapped_column(ForeignKey('recordings.id'))
    binding_revision: Mapped[int] = mapped_column(Integer)
    lidar_channel_id: Mapped[int] = mapped_column(Integer)
    ego_channel_id: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class StackReplayTickRow(Base):
    __tablename__ = 'stack_replay_ticks'
    __table_args__ = (
        ForeignKeyConstraint(['recording_id', 'ego_ordinal'], ['recording_messages.recording_id', 'recording_messages.ordinal']),
        ForeignKeyConstraint(['recording_id', 'lidar_ordinal'], ['recording_messages.recording_id', 'recording_messages.ordinal']),
    )
    job_id: Mapped[str] = mapped_column(ForeignKey('stack_replays.job_id'), primary_key=True)
    index: Mapped[int] = mapped_column(Integer, primary_key=True)
    recording_id: Mapped[str] = mapped_column(String(64))
    ego_ordinal: Mapped[int] = mapped_column(Integer)
    lidar_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    points_storage_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
