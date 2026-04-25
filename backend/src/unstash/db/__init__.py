"""Database layer — async SQLAlchemy engine, session factory, FastAPI dependency.

The application connects to PostgreSQL as the ``unstash_app`` role (DML only,
NOBYPASSRLS). Schema migrations connect as ``unstash_migrations`` and are run
exclusively by Alembic, never by application code at runtime.

Most code should use the ``get_session`` FastAPI dependency rather than
constructing engines directly.
"""

from __future__ import annotations

from unstash.db.engine import dispose_engine, get_engine
from unstash.db.session import get_session, get_sessionmaker

__all__ = [
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
]
