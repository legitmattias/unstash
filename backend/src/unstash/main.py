"""FastAPI application factory and HTTP entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated

import httpx
import redis.asyncio as redis
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from starlette.responses import Response

from sqlalchemy import text

from unstash.__about__ import __version__
from unstash.admin import admin_router
from unstash.auth import auth_backend, fastapi_users
from unstash.auth.dependencies import current_user_or_token
from unstash.auth.schemas import UserRead
from unstash.config import get_settings
from unstash.db import dispose_engine, get_engine
from unstash.db.models import User
from unstash.documents.router import documents_router
from unstash.logging import setup_logging
from unstash.orgs import orgs_router
from unstash.ratelimit import client_ip, within_fixed_window
from unstash.search.router import search_router
from unstash.startup_checks import (
    check_not_superuser,
    check_required_extensions,
    check_schema_at_head,
    check_secrets_loadable,
)

_LOGIN_PATH = "/api/auth/login"

CurrentUser = Annotated[User, Depends(current_user_or_token)]

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — startup and shutdown hooks.

    Startup runs four configuration sanity checks in order:

    1. Secret-loadability — fails before any DB call so a missing
       database_password produces a clear message rather than an opaque
       authentication failure.
    2. Database connectivity — confirms the engine can reach Postgres.
    3. Not-superuser and required-extensions — confirm the role and
       database state required for RLS and our schema actually hold.
    4. Schema-at-head — confirms the database has been migrated to the
       code's Alembic head, so stale schemas fail here instead of at
       query time.

    Each check raises ``StartupCheckError`` with an actionable message on
    failure; FastAPI propagates that as a startup error, the container exits
    non-zero, and the deploy workflow's post-deploy ``/api/ready`` probe
    sees the failed container instead of marking the deploy successful.
    """
    settings = get_settings()
    setup_logging(settings)

    logger.info(
        "starting",
        environment=settings.environment,
        version=__version__,
    )

    check_secrets_loadable(settings)

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
        await check_not_superuser(conn)
        await check_required_extensions(conn)
        await check_schema_at_head(conn)

    # Shared outbound HTTP pool for per-request inference calls (query
    # embedding, rerank) — avoids a TCP + TLS handshake per search.
    _app.state.http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=30.0),
    )
    # Redis client backing login rate limiting. Absent under ASGI test
    # transports (lifespan not run), where the middleware then no-ops.
    _app.state.redis = redis.from_url(settings.redis_url)

    try:
        yield
    finally:
        await _app.state.http_client.aclose()
        await _app.state.redis.aclose()
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

    @app.middleware("http")
    async def _rate_limit_login(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Throttle repeated login attempts per client IP.

        No-op unless the request is a login POST and a Redis client is
        present (absent under ASGI test transports). A Redis error fails
        open — a cache outage must not lock users out.
        """
        redis_client = getattr(request.app.state, "redis", None)
        if (
            request.method == "POST"
            and request.url.path == _LOGIN_PATH
            and redis_client is not None
        ):
            key = f"ratelimit:login:{client_ip(request)}"
            try:
                allowed = await within_fixed_window(
                    redis_client,
                    key,
                    limit=settings.login_rate_limit_max_attempts,
                    window_seconds=settings.login_rate_limit_window_seconds,
                )
            except Exception as exc:
                # Fail open on any Redis error — a cache outage must not
                # lock users out of login.
                logger.warning("login_rate_limit_unavailable", error=str(exc))
                allowed = True
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many login attempts. Try again later."},
                )
        return await call_next(request)

    _ = _rate_limit_login  # registered by decorator

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

    @app.get("/api/auth/me", response_model=UserRead)
    async def me(user: CurrentUser) -> User:
        """Return the authenticated user's identity."""
        return user

    _ = health  # Prevent pyright reportUnusedFunction — registered by decorator
    _ = ready
    _ = me

    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix="/api/auth",
        tags=["auth"],
    )

    app.include_router(
        admin_router,
        prefix="/api/admin",
        tags=["admin"],
    )

    app.include_router(
        orgs_router,
        prefix="/api",
        tags=["orgs"],
    )

    app.include_router(
        documents_router,
        prefix="/api",
        tags=["documents"],
    )

    app.include_router(
        search_router,
        prefix="/api",
        tags=["search"],
    )

    return app


app = create_app()
