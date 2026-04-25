"""Async session factory and FastAPI dependency.

``get_session`` is the standard way for routes and services to obtain a
database session. It opens a session-scoped transaction at the start of the
request and commits at the end (or rolls back on exception).

In M2 Phase D, this dependency will additionally execute
``SET LOCAL app.current_org_id = <uuid>`` after extracting the org id from the
URL — putting the row-level security context in place before any query runs.
That hook is intentionally not present yet; this module sets up the lifecycle
so adding it is a focused, narrow change.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from unstash.db.engine import get_engine

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
