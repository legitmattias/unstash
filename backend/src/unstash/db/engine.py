"""Async SQLAlchemy engine factory.

A single engine instance is held per process and lazily initialized on first
use. The lifespan handler in ``unstash.main`` is responsible for disposing it
on shutdown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from unstash.config import get_settings

if TYPE_CHECKING:
    from unstash.config import Settings


_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first call.

    The engine connects as ``unstash_app`` and is intended for application
    runtime use — never for migrations.
    """
    global _engine
    if _engine is None:
        _engine = _create_engine(get_settings())
    return _engine


async def dispose_engine() -> None:
    """Close the engine and release pooled connections.

    Call from the FastAPI lifespan shutdown so connections are returned to
    PostgreSQL cleanly rather than waiting for OS-level socket teardown.
    """
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def _create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,
    )
