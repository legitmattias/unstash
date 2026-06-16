"""FastAPI application factory and HTTP entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI, HTTPException

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from sqlalchemy import text

from unstash.__about__ import __version__
from unstash.config import get_settings
from unstash.db import dispose_engine, get_engine
from unstash.logging import setup_logging

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown hooks."""
    settings = get_settings()
    setup_logging(settings)

    logger.info(
        "starting",
        environment=settings.environment,
        version=__version__,
    )

    # Open a connection on startup to fail fast if the database is unreachable
    # or the configured user can't authenticate. Without this, errors only
    # surface on the first request.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    try:
        yield
    finally:
        await dispose_engine()
        logger.info("stopping")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Unstash",
        version=__version__,
        lifespan=lifespan,
        debug=settings.debug,
        docs_url="/api/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.debug else None,
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Liveness probe — returns OK if the process is running.

        Intentionally does NOT touch the database. Process-level supervisors
        (Docker, the orchestrator) use this to decide whether to restart the
        container. A failing database should not cause container restarts; a
        crashed Python process should.
        """
        return {"status": "ok", "version": __version__}

    @app.get("/api/ready")
    async def ready() -> dict[str, str]:
        """Readiness probe — returns OK if the app can serve traffic now.

        Pings the database via the connection pool. Returns 503 if the
        database is unreachable so external health checks (CI deploy checks,
        load balancers) stop sending traffic until the dependency recovers.
        """
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            # Probe is by design permissive — any failure should report
            # "not ready" rather than propagate as a 500.
            logger.warning("readiness_check_failed", error=str(exc))
            raise HTTPException(
                status_code=503,
                detail={"status": "not ready"},
            ) from exc
        return {"status": "ready", "version": __version__}

    _ = health  # Prevent pyright reportUnusedFunction — registered by decorator
    _ = ready

    return app


app = create_app()
