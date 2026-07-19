"""Database layer — async SQLAlchemy engine, session factory, FastAPI dependency.

The application connects to PostgreSQL as the ``unstash_app`` role (DML only,
NOBYPASSRLS) for ordinary org-scoped traffic, and as the ``unstash_admin`` role
(DML only, BYPASSRLS) for cross-tenant superuser admin routes. Schema migrations
connect as ``unstash_migrations`` and are run exclusively by Alembic, never by
application code at runtime.

Code obtains sessions through the FastAPI dependencies rather than
constructing engines directly: ``get_org_context`` (org-scoped routes),
``get_session_unmanaged`` (auth), and ``get_admin_session`` (superuser).
"""

from __future__ import annotations

from unstash.db.engine import dispose_engine, get_admin_engine, get_engine
from unstash.db.session import (
    get_admin_session,
    get_session_unmanaged,
    get_sessionmaker,
)

__all__ = [
    "dispose_engine",
    "get_admin_engine",
    "get_admin_session",
    "get_engine",
    "get_session_unmanaged",
    "get_sessionmaker",
]
