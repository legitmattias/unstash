"""Application-level organisation filters hold without row-level security.

Row-level security already hides another organisation's rows from the
application role. These tests run the same lookups on a ``BYPASSRLS``
connection, where no policy applies, so the only thing that can keep a
foreign row out is the explicit ``org_id`` filter in the query.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tests.integration.conftest import seed_org
from unstash.db.engine import dispose_engine, get_admin_engine, get_engine
from unstash.db.session import get_admin_sessionmaker, get_sessionmaker
from unstash.tasks.ingest import _load_targets  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def bypass_rls_session(
    settings_for_container: object,
    migrated_database: None,
) -> AsyncIterator[AsyncSession]:
    """Session on the admin role, which is not subject to any RLS policy."""
    _ = settings_for_container, migrated_database
    get_engine.cache_clear()
    get_admin_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_admin_sessionmaker.cache_clear()
    try:
        async with get_admin_sessionmaker()() as session:
            yield session
    finally:
        await dispose_engine()


async def _seed_document_and_job(
    pool: asyncpg.Pool,
    org_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    async with pool.acquire() as conn:
        document_id = await conn.fetchval(
            "INSERT INTO documents (org_id, title, source_uri, mime_type, "
            "size_bytes, content_hash, status) "
            "VALUES ($1, $2, $3, 'text/plain', 1, $4, 'pending') RETURNING id",
            org_id,
            f"doc-{suffix}",
            f"upload://{suffix}",
            f"hash-{suffix}",
        )
        job_id = await conn.fetchval(
            "INSERT INTO job_progress (org_id, task_id, task_name, status) "
            "VALUES ($1, $2, 'ingest_document', 'queued') RETURNING id",
            org_id,
            f"task-{suffix}",
        )
    return document_id, job_id


async def test_worker_target_lookup_rejects_another_orgs_ids(
    migrations_pool: asyncpg.Pool,
    bypass_rls_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task message naming another organisation's rows resolves to nothing."""
    monkeypatch.setattr("unstash.tasks.ingest._TARGET_RETRY_DELAYS", ())
    suffix = uuid.uuid4().hex[:8]
    own_org = await seed_org(migrations_pool, f"own-{suffix}", "Own")
    other_org = await seed_org(migrations_pool, f"other-{suffix}", "Other")
    other_document, other_job = await _seed_document_and_job(migrations_pool, other_org)

    document, job = await _load_targets(bypass_rls_session, own_org, other_document, other_job)

    assert document is None
    assert job is None


async def test_worker_target_lookup_finds_own_rows(
    migrations_pool: asyncpg.Pool,
    bypass_rls_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("unstash.tasks.ingest._TARGET_RETRY_DELAYS", ())
    suffix = uuid.uuid4().hex[:8]
    own_org = await seed_org(migrations_pool, f"own-{suffix}", "Own")
    own_document, own_job = await _seed_document_and_job(migrations_pool, own_org)

    document, job = await _load_targets(bypass_rls_session, own_org, own_document, own_job)

    assert document is not None
    assert document.id == own_document
    assert job is not None
    assert job.id == own_job
