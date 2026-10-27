"""Immutable component stack declarations."""
from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class StackRow(Base):
    __tablename__ = 'component_stacks'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), index=True)


class StackRevisionRow(Base):
    __tablename__ = 'component_stack_revisions'
    stack_id: Mapped[str] = mapped_column(ForeignKey('component_stacks.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40))
    manifest: Mapped[bytes] = mapped_column(LargeBinary)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    manifest_size_bytes: Mapped[int] = mapped_column(Integer)
