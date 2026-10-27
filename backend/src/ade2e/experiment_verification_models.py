"""Typed experiment plans supplement, without replacing, detection A/B plans."""
from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class ExperimentVerificationPlanRow(Base):
    __tablename__ = 'experiment_verification_plans'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('team_projects.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)

class ExperimentVerificationRevisionRow(Base):
    __tablename__ = 'experiment_verification_revisions'
    plan_id: Mapped[str] = mapped_column(ForeignKey('experiment_verification_plans.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(240), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))

class ExperimentVerificationEvaluationRow(Base):
    __tablename__ = 'experiment_verification_evaluations'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey('experiment_verification_plans.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(240), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
