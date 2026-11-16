"""The re-embed maintenance command against a real database.

A document is ingested normally, its embeddings are discarded the way a
width-changing migration discards them, and the command is expected to
bring it back to ``indexed`` through the ordinary embed task.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest

from tests.integration.conftest import login, seed_membership, seed_org, seed_user
from tests.integration.test_documents_upload import app_client  # noqa: F401 — fixture
from unstash.maintenance.requeue_embeddings import requeue_embeddings

if TYPE_CHECKING:
    import asyncpg
    from httpx import AsyncClient

USER_PASSWORD = uuid.uuid4().hex + "Aa1"
USER_EMAIL = "carol@example.com"


async def _wait_for_status(client: AsyncClient, document_id: str, status: str) -> None:
    body: object = None
    for _ in range(1800):
        response = await client.get(f"/api/orgs/acme/documents/{document_id}")
        assert response.status_code == 200
        body = response.json()
        if response.json()["status"] == status:
            return
        await asyncio.sleep(0.1)
    pytest.fail(f"document never reached {status}, last body: {body}")


async def _discard_embeddings(pool: asyncpg.Pool, document_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE chunks SET embedding = NULL WHERE document_id = $1",
            uuid.UUID(document_id),
        )
        await conn.execute(
            "UPDATE documents SET status = 'parsed', indexed_at = NULL WHERE id = $1",
            uuid.UUID(document_id),
        )


async def _unembedded_chunks(pool: asyncpg.Pool, document_id: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1 AND embedding IS NULL",
            uuid.UUID(document_id),
        )


async def test_requeue_re_embeds_parsed_documents(
    app_client: AsyncClient,  # noqa: F811 — fixture
    migrations_pool: asyncpg.Pool,
) -> None:
    user = await seed_user(migrations_pool, USER_EMAIL, USER_PASSWORD)
    org_id = await seed_org(migrations_pool, "acme", "Acme")
    await seed_membership(migrations_pool, user, org_id)
    await login(app_client, USER_EMAIL, USER_PASSWORD)

    files = {"file": ("note.txt", b"a small document about the roof\n", "text/plain")}
    response = await app_client.post("/api/orgs/acme/documents", files=files)
    assert response.status_code == 201, response.text
    document_id = response.json()["document_id"]
    await _wait_for_status(app_client, document_id, "indexed")

    # Nothing to do while every chunk has an embedding.
    assert await requeue_embeddings(dry_run=True) == 0
    assert await requeue_embeddings(dry_run=False) == 0

    await _discard_embeddings(migrations_pool, document_id)
    assert await _unembedded_chunks(migrations_pool, document_id) > 0

    # A dry run counts the document and leaves it untouched.
    assert await requeue_embeddings(dry_run=True) == 1
    assert await _unembedded_chunks(migrations_pool, document_id) > 0

    assert await requeue_embeddings(dry_run=False) == 1
    await _wait_for_status(app_client, document_id, "indexed")
    assert await _unembedded_chunks(migrations_pool, document_id) == 0

    async with migrations_pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT status, task_id, org_id FROM job_progress "
            "WHERE task_name = 'embed_document' ORDER BY created_at DESC LIMIT 1",
        )
    assert job is not None
    assert job["status"] == "succeeded"
    assert not job["task_id"].startswith("requeue-")
    assert job["org_id"] == org_id
