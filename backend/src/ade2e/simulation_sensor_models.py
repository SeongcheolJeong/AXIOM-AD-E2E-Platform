"""Additive immutable simulation sensor configuration registry."""
from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class SensorConfigurationRow(Base):
    __tablename__ = 'simulation_sensor_configurations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), index=True)


class SensorConfigurationRevisionRow(Base):
    __tablename__ = 'simulation_sensor_configuration_revisions'
    configuration_id: Mapped[str] = mapped_column(ForeignKey('simulation_sensor_configurations.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)


class SensorConfigurationIdempotencyRow(Base):
    __tablename__ = 'simulation_sensor_configuration_idempotency'
    __table_args__ = (ForeignKeyConstraint(['configuration_id', 'revision'],
        ['simulation_sensor_configuration_revisions.configuration_id', 'simulation_sensor_configuration_revisions.revision']),)
    scope: Mapped[str] = mapped_column(String(150), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    configuration_id: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
