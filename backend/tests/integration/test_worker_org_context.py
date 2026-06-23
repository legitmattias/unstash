"""Sanity check: the worker-side org context sets the right RLS GUC.

This is the worker equivalent of the request-side test in
``test_orgs.py``. Where ``get_org_scoped_session`` sets
``app.current_org_id`` for the duration of an HTTP request,
:func:`unstash.tasks.org_context` does the same for the duration of a
Taskiq job body.

The test:

1. Seeds two orgs (Acme, Beta) and an `unstash_app`-scoped connector
   row in each. ``connectors`` is RLS-protected via the policies from
   migration 0006.
2. Queues a task that opens ``org_context(acme_id)`` and counts
   connectors visible to ``unstash_app`` inside that context.
3. Asserts the count is 1 — the Acme connector — and that the
   connector's ``org_id`` is Acme's. If the GUC weren't set, RLS would
   raise; if it were set incorrectly, the wrong row would come back.

Uses an :class:`InMemoryBroker` so no Redis container is needed. The
task body still uses the real database session machinery wired to the
testcontainer Postgres, so this exercises the full GUC-set-and-query
path the production worker will run.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from taskiq import InMemoryBroker

from tests.integration.conftest import (
    TEST_APP_PASSWORD,
    TEST_MIGRATIONS_PASSWORD,
)
from unstash.config import get_settings
from unstash.db.engine import dispose_engine, get_engine
from unstash.db.models import Connector
from unstash.db.session import get_sessionmaker
from unstash.tasks import org_context

if TYPE_CHECKING:
    import asyncpg


async def _seed_org(pool: asyncpg.Pool, slug: str, name: str) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO organisations (slug, name) VALUES ($1, $2) RETURNING id",
            slug,
            name,
        )


async def _seed_connector(
    pool: asyncpg.Pool,
    org_id: uuid.UUID,
    label: str,
) -> uuid.UUID:
    """Insert a connector row directly via the migrations role (BYPASSRLS)."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO connectors (org_id, provider, status, display_name, "
            "credentials_encrypted) "
            "VALUES ($1, 'manual_upload', 'active', $2, '\\x00'::bytea) "
            "RETURNING id",
            org_id,
            label,
        )


@pytest.fixture
async def in_memory_broker() -> InMemoryBroker:
    broker = InMemoryBroker()
    await broker.startup()
    yield broker
    await broker.shutdown()


async def test_org_context_scopes_worker_queries_to_current_org(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
    migrations_pool: asyncpg.Pool,
    in_memory_broker: InMemoryBroker,
) -> None:
    """Worker job inside ``org_context(acme_id)`` sees only Acme's connector.

    Demonstrates end-to-end that the worker's context manager plays
    the same role as the request-side dependency: the GUC is set, RLS
    on ``connectors`` filters cross-tenant rows, and the worker code
    can rely on this exactly as a route handler would.
    """
    _ = migrated_database
    host, port = container_host_port

    # Point the real engine at the testcontainer database; this is what
    # ``org_context`` will reach for under the hood.
    monkeypatch.setenv("UNSTASH_DATABASE_HOST", host)
    monkeypatch.setenv("UNSTASH_DATABASE_PORT", str(port))
    monkeypatch.setenv("UNSTASH_DATABASE_NAME", "unstash")
    monkeypatch.setenv("UNSTASH_DATABASE_USER", "unstash_app")
    monkeypatch.setenv("database_password", TEST_APP_PASSWORD)
    monkeypatch.setenv("database_migrations_password", TEST_MIGRATIONS_PASSWORD)
    monkeypatch.setenv("session_secret", uuid.uuid4().hex)
    monkeypatch.setenv("encryption_key", uuid.uuid4().hex)
    monkeypatch.setenv("UNSTASH_ENVIRONMENT", "test")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    beta_id = await _seed_org(migrations_pool, "beta", "Beta")
    await _seed_connector(migrations_pool, acme_id, "Acme connector")
    await _seed_connector(migrations_pool, beta_id, "Beta connector")

    @in_memory_broker.task
    async def list_connectors_for_org(org_id_str: str) -> list[str]:
        """Inside the worker job: read connectors via org_context."""
        async with org_context(uuid.UUID(org_id_str)) as session:
            rows = (await session.execute(select(Connector))).scalars().all()
            return [row.display_name for row in rows]

    try:
        acme_task = await list_connectors_for_org.kiq(str(acme_id))
        acme_result = await acme_task.wait_result(timeout=5)
        assert not acme_result.is_err, acme_result.error
        assert acme_result.return_value == ["Acme connector"]

        beta_task = await list_connectors_for_org.kiq(str(beta_id))
        beta_result = await beta_task.wait_result(timeout=5)
        assert not beta_result.is_err, beta_result.error
        assert beta_result.return_value == ["Beta connector"]
    finally:
        await dispose_engine()


async def test_org_context_isolates_consecutive_jobs(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
    migrations_pool: asyncpg.Pool,
    in_memory_broker: InMemoryBroker,
) -> None:
    """Two jobs run back-to-back on the same broker do not leak GUC state.

    The ``is_local=true`` flag on ``set_config`` confines the GUC to
    the transaction. When a session is returned to the pool, the next
    job pulling it must start with no ``app.current_org_id``.
    Otherwise an Acme job followed by a Beta job could erroneously
    show Acme's rows in Beta's worker run.
    """
    _ = migrated_database
    host, port = container_host_port

    monkeypatch.setenv("UNSTASH_DATABASE_HOST", host)
    monkeypatch.setenv("UNSTASH_DATABASE_PORT", str(port))
    monkeypatch.setenv("UNSTASH_DATABASE_NAME", "unstash")
    monkeypatch.setenv("UNSTASH_DATABASE_USER", "unstash_app")
    monkeypatch.setenv("database_password", TEST_APP_PASSWORD)
    monkeypatch.setenv("database_migrations_password", TEST_MIGRATIONS_PASSWORD)
    monkeypatch.setenv("session_secret", uuid.uuid4().hex)
    monkeypatch.setenv("encryption_key", uuid.uuid4().hex)
    monkeypatch.setenv("UNSTASH_ENVIRONMENT", "test")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    beta_id = await _seed_org(migrations_pool, "beta", "Beta")
    await _seed_connector(migrations_pool, acme_id, "Acme only")
    await _seed_connector(migrations_pool, beta_id, "Beta only")

    @in_memory_broker.task
    async def read_one_org(org_id_str: str) -> list[str]:
        async with org_context(uuid.UUID(org_id_str)) as session:
            rows = (await session.execute(select(Connector))).scalars().all()
            return [row.display_name for row in rows]

    try:
        first = await read_one_org.kiq(str(acme_id))
        first_result = await first.wait_result(timeout=5)
        assert first_result.return_value == ["Acme only"]

        second = await read_one_org.kiq(str(beta_id))
        second_result = await second.wait_result(timeout=5)
        assert second_result.return_value == ["Beta only"]
    finally:
        await dispose_engine()
