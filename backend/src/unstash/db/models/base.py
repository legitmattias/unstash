"""Declarative base and shared mixins for SQLAlchemy models.

A consistent naming convention is set on the MetaData so Alembic
autogenerate emits stable constraint and index names. Without this, names
default to backend-specific generated strings and produce migration noise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Project-wide declarative base.

    All ORM models inherit from this. The shared MetaData carries the
    naming convention so constraint and index names are deterministic.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` columns to a model.

    ``created_at`` is set by the database on insert and never modified.
    ``updated_at`` is set by the database on insert and refreshed via
    SQLAlchemy's ``onupdate`` hook on subsequent updates through the ORM.
    Raw SQL updates that bypass the ORM will not refresh ``updated_at``
    — a database trigger would close that gap if it becomes a concern.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
