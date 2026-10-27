"""Project access tokens: one user label and role per explicit project boundary."""
from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class ProjectRow(Base):
    __tablename__ = 'team_projects'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[str] = mapped_column(String(40))

class ProjectTokenRow(Base):
    __tablename__ = 'team_project_tokens'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('team_projects.id'), index=True)
    user_label: Mapped[str] = mapped_column(String(180))
    role: Mapped[str] = mapped_column(String(24))
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[str] = mapped_column(String(40))
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ObjectBetaPolicyRow(Base):
    """Operator opt-in; shared by API and worker processes, never browser state."""
    __tablename__ = 'object_beta_project_policies'
    project_id: Mapped[str] = mapped_column(ForeignKey('team_projects.id'), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    resource_id: Mapped[str] = mapped_column(String(180))
    updated_at: Mapped[str] = mapped_column(String(40))
