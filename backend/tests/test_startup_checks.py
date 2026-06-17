"""Tests for the startup configuration checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from unstash.config import Settings
from unstash.startup_checks import (
    REQUIRED_EXTENSIONS,
    REQUIRED_SECRETS,
    StartupCheckError,
    check_not_superuser,
    check_required_extensions,
    check_secrets_loadable,
)

# ---------------------------------------------------------------------------
# check_secrets_loadable
# ---------------------------------------------------------------------------


def _settings_with_all_secrets() -> Settings:
    """Build a Settings instance with every required secret set to a placeholder."""
    return Settings(
        **{name: f"value-for-{name}" for name in REQUIRED_SECRETS},
    )


def test_check_secrets_loadable_passes_when_all_present() -> None:
    settings = _settings_with_all_secrets()
    # Should not raise.
    check_secrets_loadable(settings)


def test_check_secrets_loadable_raises_when_one_missing() -> None:
    overrides = {name: f"value-for-{name}" for name in REQUIRED_SECRETS}
    overrides["database_migrations_password"] = ""
    settings = Settings(**overrides)

    with pytest.raises(StartupCheckError) as exc_info:
        check_secrets_loadable(settings)

    message = str(exc_info.value)
    assert "database_migrations_password" in message
    assert "/run/secrets/" in message
    # Other secrets should not appear in the failure message.
    assert "database_password" not in message.replace(
        "database_migrations_password",
        "",
    )


def test_check_secrets_loadable_lists_all_missing_secrets() -> None:
    overrides = {name: f"value-for-{name}" for name in REQUIRED_SECRETS}
    overrides["session_secret"] = ""
    overrides["encryption_key"] = ""
    settings = Settings(**overrides)

    with pytest.raises(StartupCheckError) as exc_info:
        check_secrets_loadable(settings)

    message = str(exc_info.value)
    assert "session_secret" in message
    assert "encryption_key" in message


# ---------------------------------------------------------------------------
# check_not_superuser
# ---------------------------------------------------------------------------


def _mock_conn_returning(value: str) -> AsyncMock:
    """Build a mock AsyncConnection that returns ``value`` from execute().scalar_one()."""
    conn = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = value
    conn.execute = AsyncMock(return_value=result)
    return conn


async def test_check_not_superuser_passes_when_off() -> None:
    conn = _mock_conn_returning("off")
    await check_not_superuser(conn)


async def test_check_not_superuser_raises_when_on() -> None:
    conn = _mock_conn_returning("on")

    with pytest.raises(StartupCheckError) as exc_info:
        await check_not_superuser(conn)

    message = str(exc_info.value)
    assert "superuser" in message.lower()
    assert "NOSUPERUSER" in message


# ---------------------------------------------------------------------------
# check_required_extensions
# ---------------------------------------------------------------------------


def _mock_conn_returning_extensions(extensions: list[str]) -> AsyncMock:
    """Build a mock AsyncConnection whose execute() yields the given extension rows."""
    conn = AsyncMock()
    result = MagicMock()
    # The check iterates the result; mock it as an iterable of single-column rows.
    result.__iter__ = lambda self: iter([(ext,) for ext in extensions])
    conn.execute = AsyncMock(return_value=result)
    return conn


async def test_check_required_extensions_passes_when_all_present() -> None:
    conn = _mock_conn_returning_extensions(list(REQUIRED_EXTENSIONS))
    await check_required_extensions(conn)


async def test_check_required_extensions_raises_when_one_missing() -> None:
    present = [ext for ext in REQUIRED_EXTENSIONS if ext != "vectorscale"]
    conn = _mock_conn_returning_extensions(present)

    with pytest.raises(StartupCheckError) as exc_info:
        await check_required_extensions(conn)

    message = str(exc_info.value)
    assert "vectorscale" in message
    assert "init-db.sh" in message


async def test_check_required_extensions_lists_all_missing() -> None:
    conn = _mock_conn_returning_extensions(["citext"])

    with pytest.raises(StartupCheckError) as exc_info:
        await check_required_extensions(conn)

    message = str(exc_info.value)
    for missing in ("vector", "vectorscale", "pg_search"):
        assert missing in message
