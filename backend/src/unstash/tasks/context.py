"""Worker-side org context manager.

The HTTP path has :func:`unstash.orgs.router.get_org_scoped_session`,
which opens a transaction and sets ``app.current_org_id`` so RLS
policies scope queries to the request's tenant. Workers need the same
thing but the entry point is a Taskiq job kwarg, not a URL slug.

This module provides :func:`org_context` — an async context manager
that opens a session, starts a transaction, runs
``set_config('app.current_org_id', :org_id, true)``, and yields the
session for the job body to use. Commits on clean exit, rolls back on
exception, closes either way.

Closes issue #38 (M2 Phase D3 — worker-side org context).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text

from unstash.db.session import get_sessionmaker

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def org_context(org_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Yield a session bound to the worker's org context.

    Opens a session-scoped transaction, sets the
    ``app.current_org_id`` GUC for the transaction via the
    ``set_config`` function form (which accepts query parameters,
    unlike literal ``SET LOCAL``), then yields the session for the
    job body. Commits on clean exit, rolls back on exception.

    The ``is_local=true`` flag confines the GUC to the current
    transaction, so it cannot leak to a subsequent job when the
    connection is returned to the pool.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)").bindparams(
                org_id=str(org_id),
            ),
        )
        yield session
