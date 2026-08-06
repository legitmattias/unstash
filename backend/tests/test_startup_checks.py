"""Tests for the startup configuration checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from unstash.config import Settings
from unstash.db.models.chunk import EMBEDDING_DIM
from unstash.startup_checks import (
    REQUIRED_EXTENSIONS,
    REQUIRED_SECRETS,
    StartupCheckError,
    check_embedding_dimensions,
    check_not_superuser,
    check_required_extensions,
    check_schema_at_head,
    check_secrets_loadable,
)

BACKEND_ROOT = Path(__file__).parents[1]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"

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


# ---------------------------------------------------------------------------
# check_schema_at_head
# ---------------------------------------------------------------------------


def _code_head() -> str:
    """The migration head declared by the real alembic scripts."""
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()[0]


def _mock_conn_with_schema(
    regclass: str | None,
    revisions: list[str],
) -> AsyncMock:
    """Mock AsyncConnection for the schema check's two queries in order."""
    conn = AsyncMock()
    regclass_result = MagicMock()
    regclass_result.scalar_one.return_value = regclass
    rows_result = MagicMock()
    rows_result.__iter__ = lambda self: iter([(rev,) for rev in revisions])
    conn.execute = AsyncMock(side_effect=[regclass_result, rows_result])
    return conn


async def test_check_schema_at_head_passes_at_head() -> None:
    conn = _mock_conn_with_schema("alembic_version", [_code_head()])
    await check_schema_at_head(conn, alembic_ini=ALEMBIC_INI)


async def test_check_schema_at_head_raises_when_never_migrated() -> None:
    conn = _mock_conn_with_schema(None, [])

    with pytest.raises(StartupCheckError) as exc_info:
        await check_schema_at_head(conn, alembic_ini=ALEMBIC_INI)

    message = str(exc_info.value)
    assert "never been migrated" in message
    assert "alembic upgrade head" in message


async def test_check_schema_at_head_raises_on_stale_revision() -> None:
    conn = _mock_conn_with_schema("alembic_version", ["0001"])

    with pytest.raises(StartupCheckError) as exc_info:
        await check_schema_at_head(conn, alembic_ini=ALEMBIC_INI)

    message = str(exc_info.value)
    assert "0001" in message
    assert _code_head() in message


async def test_check_schema_at_head_raises_when_ini_missing(
    tmp_path: Path,
) -> None:
    conn = AsyncMock()

    with pytest.raises(StartupCheckError) as exc_info:
        await check_schema_at_head(conn, alembic_ini=tmp_path / "missing.ini")

    assert "alembic.ini not found" in str(exc_info.value)
    conn.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# check_embedding_dimensions
# ---------------------------------------------------------------------------


def test_check_embedding_dimensions_passes_when_matching() -> None:
    settings = Settings(
        **{name: f"value-for-{name}" for name in REQUIRED_SECRETS},
        jina_embedding_dimensions=EMBEDDING_DIM,
    )
    check_embedding_dimensions(settings)


def test_check_embedding_dimensions_rejects_mismatch() -> None:
    """A provider whose width differs from the column must fail at boot."""
    settings = Settings(
        **{name: f"value-for-{name}" for name in REQUIRED_SECRETS},
        jina_embedding_dimensions=EMBEDDING_DIM // 2,
    )
    with pytest.raises(StartupCheckError) as excinfo:
        check_embedding_dimensions(settings)
    message = str(excinfo.value)
    assert str(EMBEDDING_DIM) in message
    assert str(EMBEDDING_DIM // 2) in message
