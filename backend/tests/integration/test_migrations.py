"""Migration roundtrip and schema-cleanliness tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config

if TYPE_CHECKING:
    from pathlib import Path

    import asyncpg

    from unstash.config import Settings


def _alembic_config(backend_root: Path) -> Config:
    return Config(str(backend_root / "alembic.ini"))


async def test_downgrade_to_base_leaves_only_alembic_version(
    migrations_pool: asyncpg.Pool,
    settings_for_container: Settings,
    backend_root: Path,
) -> None:
    """After downgrade base, the public schema holds only alembic_version."""
    _ = settings_for_container
    config = _alembic_config(backend_root)

    command.downgrade(config, "base")

    try:
        async with migrations_pool.acquire() as conn:
            tables = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
            table_names = [row["tablename"] for row in tables]
    finally:
        command.upgrade(config, "head")

    assert table_names == ["alembic_version"], (
        f"unexpected tables after downgrade base: {table_names}"
    )


async def test_upgrade_head_produces_expected_table_set(
    migrations_pool: asyncpg.Pool,
) -> None:
    """After the fixture's upgrade head, exactly the expected tables exist."""
    expected = {
        "access_tokens",
        "alembic_version",
        "audit_log",
        "chunks",
        "connectors",
        "documents",
        "job_progress",
        "org_memberships",
        "organisations",
        "search_logs",
        "users",
    }

    async with migrations_pool.acquire() as conn:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        actual = {row["tablename"] for row in rows}

    assert actual == expected


async def test_extensions_remain_after_roundtrip(
    migrations_pool: asyncpg.Pool,
    settings_for_container: Settings,
    backend_root: Path,
) -> None:
    """Extensions installed by init-db.sh survive a downgrade/upgrade roundtrip.

    Migrations do not own the extensions; init-db.sh installed them at first
    boot. Downgrade should leave them alone.
    """
    _ = settings_for_container
    config = _alembic_config(backend_root)

    command.downgrade(config, "base")

    try:
        async with migrations_pool.acquire() as conn:
            rows = await conn.fetch("SELECT extname FROM pg_extension")
            present = {row["extname"] for row in rows}
    finally:
        command.upgrade(config, "head")

    for required in ("citext", "vector", "vectorscale", "pg_search"):
        assert required in present, (
            f"extension dropped during downgrade (should not happen): {required}"
        )
