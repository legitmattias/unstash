"""Async SQLAlchemy engine factory.

A single engine instance is held per process via ``functools.lru_cache``. The
lifespan handler in ``unstash.main`` is responsible for disposing it on
shutdown.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from unstash.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy import URL

    from unstash.config import Settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first call.

    The engine connects as ``unstash_app`` and is intended for application
    runtime use — never for migrations, never for cross-tenant admin work.
    """
    return _create_engine(get_settings().database_url, get_settings())


@lru_cache(maxsize=1)
def get_admin_engine() -> AsyncEngine:
    """Return the process-wide admin async engine, creating it on first call.

    The engine connects as ``unstash_admin``, which has ``BYPASSRLS`` and is
    used exclusively by the superuser-gated routes in ``unstash.admin``. It
    runs alongside the application engine, on a separate connection pool, so
    the application path can never accidentally reuse the admin credential.
    See ``docs/adr/0006-auth-and-cross-tenant-admin.md``.
    """
    return _create_engine(get_settings().database_admin_url, get_settings())


async def dispose_engine() -> None:
    """Close the engines and release pooled connections.

    Call from the FastAPI lifespan shutdown so connections are returned to
    PostgreSQL cleanly rather than waiting for OS-level socket teardown.
    """
    if get_engine.cache_info().currsize > 0:
        engine = get_engine()
        await engine.dispose()
        get_engine.cache_clear()
    if get_admin_engine.cache_info().currsize > 0:
        admin_engine = get_admin_engine()
        await admin_engine.dispose()
        get_admin_engine.cache_clear()


def _create_engine(url: URL, settings: Settings) -> AsyncEngine:
    return create_async_engine(
        url,
        echo=settings.debug,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,
    )
