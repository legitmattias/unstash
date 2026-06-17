"""Row-Level Security adversarial tests.

These tests prove that the policies from migration 0006 actually constrain
the unstash_app role to its own tenant's rows, even when the application
constructs queries that try to escape the policy. They form the runtime
half of the multi-tenant promise; the schema half is the migration itself.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

# Tables that should have RLS enabled. Mirrors TENANT_SCOPED_TABLES in
# backend/alembic/versions/0006_rls_policies.py.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "org_memberships",
    "documents",
    "chunks",
    "connectors",
    "search_logs",
    "job_progress",
    "audit_log",
)


# ---------------------------------------------------------------------------
# Meta-test: every tenant-scoped table has RLS enabled.
# ---------------------------------------------------------------------------


async def test_rls_enabled_on_every_tenant_scoped_table(
    migrations_pool: asyncpg.Pool,
) -> None:
    """pg_class.relrowsecurity is true for every tenant-scoped table."""
    async with migrations_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY($1::text[])",
            list(TENANT_SCOPED_TABLES),
        )
        rls_state = {row["relname"]: row["relrowsecurity"] for row in rows}

    missing = [t for t in TENANT_SCOPED_TABLES if t not in rls_state]
    assert not missing, f"tables not found in pg_class: {missing}"

    disabled = [t for t, on in rls_state.items() if not on]
    assert not disabled, f"RLS not enabled on: {disabled}"


async def test_tenant_isolation_policy_exists_on_every_table(
    migrations_pool: asyncpg.Pool,
) -> None:
    """Every tenant-scoped table has the expected tenant_isolation policy."""
    async with migrations_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT tablename, policyname, cmd FROM pg_policies "
            "WHERE policyname = 'tenant_isolation'"
        )
        policies = {row["tablename"]: row for row in rows}

    missing = [t for t in TENANT_SCOPED_TABLES if t not in policies]
    assert not missing, f"tenant_isolation policy missing on: {missing}"

    wrong_cmd = [t for t, row in policies.items() if row["cmd"] != "ALL"]
    assert not wrong_cmd, f"policy cmd is not ALL on: {wrong_cmd}"


# ---------------------------------------------------------------------------
# Adversarial: cross-tenant isolation actually holds at query time.
# ---------------------------------------------------------------------------


async def _seed_two_orgs_with_connectors(
    migrations_pool: asyncpg.Pool,
) -> tuple[uuid.UUID, uuid.UUID, str, str]:
    """Seed two orgs and a connector for each as unstash_migrations.

    Returns (org_a_id, org_b_id, slug_a, slug_b).
    """
    suffix = uuid.uuid4().hex[:8]
    slug_a = f"alpha-{suffix}"
    slug_b = f"beta-{suffix}"

    async with migrations_pool.acquire() as conn:
        org_a = await conn.fetchval(
            "INSERT INTO organisations (name, slug) VALUES ($1, $2) RETURNING id",
            f"Alpha {suffix}",
            slug_a,
        )
        org_b = await conn.fetchval(
            "INSERT INTO organisations (name, slug) VALUES ($1, $2) RETURNING id",
            f"Beta {suffix}",
            slug_b,
        )
        await conn.execute(
            "INSERT INTO connectors "
            "(org_id, provider, display_name, credentials_encrypted) "
            "VALUES ($1, 'manual_upload', $2, '\\x00')",
            org_a,
            f"Alpha connector {suffix}",
        )
        await conn.execute(
            "INSERT INTO connectors "
            "(org_id, provider, display_name, credentials_encrypted) "
            "VALUES ($1, 'manual_upload', $2, '\\x00')",
            org_b,
            f"Beta connector {suffix}",
        )

    return org_a, org_b, slug_a, slug_b


async def test_app_role_with_no_context_fails_loudly(
    app_pool: asyncpg.Pool,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Querying as unstash_app without setting app.current_org_id raises."""
    await _seed_two_orgs_with_connectors(migrations_pool)

    async with app_pool.acquire() as conn, conn.transaction():
        with pytest.raises(asyncpg.UndefinedObjectError) as exc_info:
            await conn.fetch("SELECT count(*) FROM connectors")

    assert "app.current_org_id" in str(exc_info.value)


async def test_app_role_with_context_a_sees_only_a(
    app_pool: asyncpg.Pool,
    migrations_pool: asyncpg.Pool,
) -> None:
    """With app.current_org_id set to org A, only A's connector is visible."""
    org_a, _, _, _ = await _seed_two_orgs_with_connectors(migrations_pool)

    async with app_pool.acquire() as conn, conn.transaction():
        await conn.execute(f"SET LOCAL app.current_org_id = '{org_a}'")
        rows = await conn.fetch("SELECT org_id FROM connectors")

    assert len(rows) == 1
    assert rows[0]["org_id"] == org_a


async def test_app_role_with_context_b_sees_only_b(
    app_pool: asyncpg.Pool,
    migrations_pool: asyncpg.Pool,
) -> None:
    """With app.current_org_id set to org B, only B's connector is visible."""
    _, org_b, _, _ = await _seed_two_orgs_with_connectors(migrations_pool)

    async with app_pool.acquire() as conn, conn.transaction():
        await conn.execute(f"SET LOCAL app.current_org_id = '{org_b}'")
        rows = await conn.fetch("SELECT org_id FROM connectors")

    assert len(rows) == 1
    assert rows[0]["org_id"] == org_b


async def test_constructed_query_cannot_escape_rls(
    app_pool: asyncpg.Pool,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A WHERE clause naming org B's id while context is A still returns zero.

    This is the canonical RLS adversarial probe — the application layer
    forgets a filter or has a bug, and the policy is the last line of defence.
    """
    org_a, org_b, _, _ = await _seed_two_orgs_with_connectors(migrations_pool)

    async with app_pool.acquire() as conn, conn.transaction():
        await conn.execute(f"SET LOCAL app.current_org_id = '{org_a}'")
        rows = await conn.fetch(
            "SELECT org_id FROM connectors WHERE org_id = $1",
            org_b,
        )

    assert rows == []


async def test_with_check_blocks_cross_tenant_insert(
    app_pool: asyncpg.Pool,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Inserting a row with another tenant's org_id is rejected by WITH CHECK."""
    org_a, org_b, _, _ = await _seed_two_orgs_with_connectors(migrations_pool)

    async with app_pool.acquire() as conn, conn.transaction():
        await conn.execute(f"SET LOCAL app.current_org_id = '{org_a}'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError) as exc_info:
            await conn.execute(
                "INSERT INTO connectors "
                "(org_id, provider, display_name, credentials_encrypted) "
                "VALUES ($1, 'manual_upload', $2, '\\x00')",
                org_b,
                "Cross-tenant smuggle attempt",
            )

    assert "row-level security" in str(exc_info.value).lower()


async def test_migrations_role_bypasses_rls(
    migrations_pool: asyncpg.Pool,
) -> None:
    """unstash_migrations sees rows from every org regardless of context."""
    await _seed_two_orgs_with_connectors(migrations_pool)

    async with migrations_pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM connectors")

    assert count >= 2
