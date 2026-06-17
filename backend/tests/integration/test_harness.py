"""Sanity test for the testcontainers harness — verifies it boots correctly."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


async def test_harness_can_connect_as_app_role(app_pool: asyncpg.Pool) -> None:
    """The app pool is connected as unstash_app with the right database."""
    async with app_pool.acquire() as conn:
        current_user = await conn.fetchval("SELECT current_user")
        current_db = await conn.fetchval("SELECT current_database()")
        is_superuser = await conn.fetchval("SELECT current_setting('is_superuser')")

    assert current_user == "unstash_app"
    assert current_db == "unstash"
    assert is_superuser == "off"


async def test_harness_can_connect_as_migrations_role(
    migrations_pool: asyncpg.Pool,
) -> None:
    """The migrations pool is connected as unstash_migrations with BYPASSRLS."""
    async with migrations_pool.acquire() as conn:
        current_user = await conn.fetchval("SELECT current_user")
        bypass_rls = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )

    assert current_user == "unstash_migrations"
    assert bypass_rls is True


async def test_harness_has_required_extensions(
    migrations_pool: asyncpg.Pool,
) -> None:
    """init-db.sh installed all four required extensions."""
    async with migrations_pool.acquire() as conn:
        rows = await conn.fetch("SELECT extname FROM pg_extension")
        present = {row["extname"] for row in rows}

    for required in ("citext", "vector", "vectorscale", "pg_search"):
        assert required in present, f"missing extension: {required}"
