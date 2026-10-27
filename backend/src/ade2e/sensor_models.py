"""Immutable rig/binding manifests with real recording/channel relationships."""

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class SensorRigRow(Base):
    __tablename__ = "sensor_rigs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), index=True)


class SensorRigRevisionRow(Base):
    __tablename__ = "sensor_rig_revisions"
    rig_id: Mapped[str] = mapped_column(ForeignKey("sensor_rigs.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class RecordingSensorBindingRow(Base):
    __tablename__ = "recording_sensor_bindings"
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))


class RecordingSensorBindingRevisionRow(Base):
    __tablename__ = "recording_sensor_binding_revisions"
    __table_args__ = (ForeignKeyConstraint(["rig_id", "rig_revision"], ["sensor_rig_revisions.rig_id", "sensor_rig_revisions.revision"]),)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recording_sensor_bindings.recording_id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    rig_id: Mapped[str] = mapped_column(String(64))
    rig_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class RecordingSensorBindingChannelRow(Base):
    __tablename__ = "recording_sensor_binding_channels"
    __table_args__ = (
        ForeignKeyConstraint(["recording_id", "revision"], ["recording_sensor_binding_revisions.recording_id", "recording_sensor_binding_revisions.revision"]),
        ForeignKeyConstraint(["recording_id", "channel_id"], ["recording_channels.recording_id", "recording_channels.channel_id"]),
        UniqueConstraint("recording_id", "revision", "sensor_key"),
    )
    recording_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sensor_key: Mapped[str] = mapped_column(String(64))
