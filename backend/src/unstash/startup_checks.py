"""Startup-time configuration validations.

Each check is called from the FastAPI lifespan handler. A failed check
raises ``StartupCheckError`` with a clear, actionable message; FastAPI
propagates that as a startup error, the container exits non-zero, and the
deploy workflow's post-deploy ``/api/ready`` probe sees a failed
container instead of marking the deploy successful.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

    from unstash.config import Settings

logger = structlog.get_logger(__name__)


class StartupCheckError(RuntimeError):
    """Raised when a startup check finds an unrecoverable configuration problem."""


# Secrets that the application must have loaded at startup. Listed explicitly
# (rather than discovered via the alias-field convention) so that adding a new
# secret to ``config.Settings`` is a deliberate two-step: declare the field,
# then add it here.
REQUIRED_SECRETS: tuple[str, ...] = (
    "database_password",
    "database_migrations_password",
    "session_secret",
    "encryption_key",
)

# Extensions that the database must have installed. Mirrors the CREATE EXTENSION
# calls in ``docker/init-db.sh``.
REQUIRED_EXTENSIONS: tuple[str, ...] = (
    "citext",
    "vector",
    "vectorscale",
    "pg_search",
)


def check_secrets_loadable(settings: Settings) -> None:
    """Confirm every required secret has been loaded with a non-empty value.

    Pydantic Settings reads each secret file at instance construction time; a
    missing file leaves the field at its empty-string default. This check
    converts that silent default into a loud startup failure naming the
    missing secrets and where to put them.
    """
    missing: list[str] = []
    for name in REQUIRED_SECRETS:
        # Defensive: catches if a secret in REQUIRED_SECRETS no longer exists
        # in the Settings model.
        if not hasattr(settings, name):
            missing.append(f"{name} (not declared in Settings)")
            continue
        value = getattr(settings, name, "")
        if not value:
            missing.append(name)

    if missing:
        raise StartupCheckError(
            "Required secrets not loaded: "
            f"{sorted(missing)}. Check that each secret file exists at "
            "/run/secrets/<name> (or that the corresponding environment "
            "variable is set in tests)."
        )

    logger.info("startup_check_passed", check="secrets_loadable")


async def check_not_superuser(conn: AsyncConnection) -> None:
    """Confirm the application is not connected as a Postgres superuser.

    Superusers bypass Row-Level Security unconditionally; a superuser
    application role would silently disable multi-tenant isolation.
    """
    result = await conn.execute(text("SELECT current_setting('is_superuser')"))
    value = result.scalar_one()
    # current_setting returns 'on' or 'off' as a string.
    if value == "on":
        raise StartupCheckError(
            "Application is connected as a Postgres superuser. This bypasses "
            "Row-Level Security and breaks multi-tenant isolation. Check the "
            "DATABASE_USER setting and the role configuration in init-db.sh; "
            "the application role must have NOSUPERUSER."
        )

    logger.info("startup_check_passed", check="not_superuser")


async def check_required_extensions(conn: AsyncConnection) -> None:
    """Confirm every required Postgres extension is installed in the database.

    Extensions are installed at first boot by ``docker/init-db.sh``.
    Migrations reference them via CREATE EXTENSION IF NOT EXISTS but rely
    on init-db.sh having run successfully.
    """
    result = await conn.execute(
        text("SELECT extname FROM pg_extension WHERE extname = ANY(:names)"),
        {"names": list(REQUIRED_EXTENSIONS)},
    )
    present = {row[0] for row in result}
    missing = sorted(set(REQUIRED_EXTENSIONS) - present)

    if missing:
        raise StartupCheckError(
            f"Required Postgres extensions not installed: {missing}. "
            "These are expected to be installed by docker/init-db.sh at first "
            "boot. Check that the postgres container is using the custom "
            "image and that init-db.sh ran successfully."
        )

    logger.info("startup_check_passed", check="required_extensions")
