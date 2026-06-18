"""Database layer — async SQLAlchemy engine, session factory, FastAPI dependency.

The application connects to PostgreSQL as the ``unstash_app`` role (DML only,
NOBYPASSRLS) for ordinary org-scoped traffic, and as the ``unstash_admin`` role
(DML only, BYPASSRLS) for cross-tenant superuser admin routes. Schema migrations
connect as ``unstash_migrations`` and are run exclusively by Alembic, never by
application code at runtime.

Most code should use the ``get_session`` FastAPI dependency rather than
constructing engines directly. Admin routes use ``get_admin_session``.
"""

from __future__ import annotations

from unstash.db.engine import dispose_engine, get_admin_engine, get_engine
from unstash.db.session import (
    get_admin_session,
    get_session,
    get_session_unmanaged,
    get_sessionmaker,
)

__all__ = [
    "dispose_engine",
    "get_admin_engine",
    "get_admin_session",
    "get_engine",
    "get_session",
    "get_session_unmanaged",
    "get_sessionmaker",
]
