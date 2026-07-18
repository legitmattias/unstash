"""Async session factory and FastAPI dependency.

``get_session`` opens a session-scoped transaction at the start of the
request and commits at the end (or rolls back on exception).

Org-scoped routes do not use this dependency directly; they go through
:func:`unstash.orgs.dependencies.get_org_context`, which sets
``app.current_org_id`` on the transaction — establishing the row-level
security context — before any query runs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from unstash.db.engine import get_admin_engine, get_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async sessionmaker.

    ``expire_on_commit=False`` is mandatory for async SQLAlchemy: with the
    default ``True``, attribute access after commit triggers a lazy load,
    which is invalid in async context and produces ``MissingGreenlet`` errors.
    Setting it to ``False`` means attributes loaded inside the transaction
    remain accessible after commit, which is what async code expects.
    """
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


@lru_cache(maxsize=1)
def get_admin_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async sessionmaker for the admin engine.

    Bound to the ``unstash_admin`` role (BYPASSRLS). Used exclusively by
    superuser-gated routes in ``unstash.admin``. See ``get_admin_engine``.
    """
    return async_sessionmaker(
        bind=get_admin_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session inside a transaction.

    The session is opened, a transaction is started, the route body runs, and
    the transaction commits on clean exit or rolls back on exception. The
    session is then closed, releasing the connection to the pool.

    Usage::

        @app.get("/something")
        async def handler(session: Annotated[AsyncSession, Depends(get_session)]):
            result = await session.execute(...)
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        yield session


async def get_session_unmanaged() -> AsyncIterator[AsyncSession]:
    """Like get_session, but without an enclosing transaction.

    For consumers that manage their own commits — notably FastAPI-Users'
    database adapters, which call ``session.commit()`` directly inside the
    request handler. Wrapping such a consumer in our own ``session.begin()``
    causes ``Can't operate on closed transaction inside context manager``.

    Routes that do not need an enclosing transaction (auth) use this
    dependency. Org-scoped routes that need RLS context use the
    transaction-wrapping :func:`unstash.orgs.dependencies.get_org_context`.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def get_admin_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an admin-engine session, no enclosing transaction.

    The admin engine is bound to ``unstash_admin`` (BYPASSRLS). Routes that
    use this dependency must be gated by superuser checks at the application
    layer — never expose this dependency on a route a non-superuser can
    reach. The handler is responsible for committing or rolling back.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        yield session
