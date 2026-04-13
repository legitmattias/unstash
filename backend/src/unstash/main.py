"""FastAPI application factory and HTTP entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from unstash.__about__ import __version__
from unstash.config import get_settings
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
    yield
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
        """Liveness probe — returns OK if the process is running."""
        return {"status": "ok", "version": __version__}

    _ = health  # Prevent pyright reportUnusedFunction — registered by decorator

    return app


app = create_app()
