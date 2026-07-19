"""End-to-end upload + lifecycle tests for the documents routes.

Exercises the full loop: POST upload streams a file to disk, inserts
pending document and queued job rows, queues ingestion; the in-memory
Taskiq broker runs parse then embed; the document reaches ``indexed``
and the job ``succeeded``; the monitoring routes surface the state.

Cross-org isolation is exercised explicitly: a second org's
authenticated user cannot see the first org's documents through any
of the listing or single-resource routes.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.conftest import (
    TEST_ADMIN_PASSWORD,
    TEST_APP_PASSWORD,
    TEST_MIGRATIONS_PASSWORD,
    login,
    seed_membership,
    seed_org,
    seed_user,
)
from unstash.config import get_settings
from unstash.db.engine import dispose_engine, get_admin_engine, get_engine
from unstash.db.session import get_admin_sessionmaker, get_sessionmaker
from unstash.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import asyncpg


USER_PASSWORD = uuid.uuid4().hex + "Aa1"
USER_A_EMAIL = "alice@example.com"
USER_B_EMAIL = "bob@example.com"


@pytest.fixture
async def app_client(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
    tmp_path: Path,
) -> AsyncIterator[AsyncClient]:
    """ASGI client with a temp documents_root and in-memory Taskiq broker."""
    _ = migrated_database
    host, port = container_host_port
    docs_root = tmp_path / "documents"
    docs_root.mkdir(parents=True)
    monkeypatch.setenv("UNSTASH_DATABASE_HOST", host)
    monkeypatch.setenv("UNSTASH_DATABASE_PORT", str(port))
    monkeypatch.setenv("UNSTASH_DATABASE_NAME", "unstash")
    monkeypatch.setenv("UNSTASH_DATABASE_USER", "unstash_app")
    monkeypatch.setenv("database_password", TEST_APP_PASSWORD)
    monkeypatch.setenv("database_migrations_password", TEST_MIGRATIONS_PASSWORD)
    monkeypatch.setenv("database_admin_password", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("session_secret", uuid.uuid4().hex)
    monkeypatch.setenv("encryption_key", uuid.uuid4().hex)
    monkeypatch.setenv("UNSTASH_ENVIRONMENT", "test")
    monkeypatch.setenv("UNSTASH_DOCUMENTS_ROOT", str(docs_root))
    monkeypatch.setenv("UNSTASH_EMBEDDER_BACKEND", "fake")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_admin_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_admin_sessionmaker.cache_clear()

    # The in-memory Taskiq broker requires startup before tasks are
    # sent. The application-level lifespan doesn't call broker.startup
    # because in production Taskiq runs in its own worker process — so
    # the test does it directly. UNSTASH_TASKIQ_IN_MEMORY=1 is set at
    # the top of this conftest, so the broker imported here is the
    # in-memory one (set at module load).
    from unstash.tasks import broker  # noqa: PLC0415

    await broker.startup()

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        await broker.shutdown()
        await dispose_engine()


async def test_upload_round_trip(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Small file uploads, runs parse + embed, and lands indexed in monitoring."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await seed_org(migrations_pool, "acme", "Acme")
    await seed_membership(migrations_pool, user_a, acme_id)

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)

    payload = b"hello world, this is a tiny test document\n"
    files = {"file": ("hello.txt", payload, "text/plain")}
    response = await app_client.post("/api/orgs/acme/documents", files=files)
    assert response.status_code == 201, response.text
    body = response.json()
    document_id = body["document_id"]
    job_id = body["job_id"]

    # Generous ceiling: the first parse in a run may cold-load models.
    for _ in range(1800):
        doc = await app_client.get(f"/api/orgs/acme/documents/{document_id}")
        assert doc.status_code == 200
        if doc.json()["status"] == "indexed":
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail(f"document never reached indexed, last body: {doc.json()}")

    job = await app_client.get(f"/api/orgs/acme/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"
    assert job.json()["started_at"] is not None
    assert job.json()["finished_at"] is not None


async def test_upload_writes_file_with_correct_hash(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
    tmp_path: Path,
) -> None:
    """The file lands on disk under {root}/{org_id}/{document_id}/ with the right SHA-256."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await seed_org(migrations_pool, "acme", "Acme")
    await seed_membership(migrations_pool, user_a, acme_id)

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)

    payload = b"some bytes for verification"
    files = {"file": ("verify.txt", payload, "text/plain")}
    response = await app_client.post("/api/orgs/acme/documents", files=files)
    assert response.status_code == 201
    document_id = response.json()["document_id"]

    doc = await app_client.get(f"/api/orgs/acme/documents/{document_id}")
    body = doc.json()

    expected_hash = hashlib.sha256(payload).hexdigest()
    assert body["content_hash"] == expected_hash
    assert body["size_bytes"] == len(payload)

    # The on-disk path is recoverable from settings + ids.
    docs_root = tmp_path / "documents"
    on_disk = docs_root / str(acme_id) / document_id / "verify.txt"
    assert on_disk.read_bytes() == payload


async def _wait_terminal(client: AsyncClient, slug: str, document_id: str) -> str:
    """Poll until ingestion is terminal so no task outlives the test.

    A task still running at fixture teardown leaks its DB connection and
    trips ``filterwarnings = error`` in an unrelated test.
    """
    body: dict = {}
    for _ in range(1800):
        doc = await client.get(f"/api/orgs/{slug}/documents/{document_id}")
        assert doc.status_code == 200
        body = doc.json()
        if body["status"] in {"indexed", "failed"}:
            return body["status"]
        await asyncio.sleep(0.1)
    pytest.fail(f"document never terminal, last body: {body}")


async def test_list_documents_paginates(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """``GET /documents`` returns newest first with limit/offset semantics."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await seed_org(migrations_pool, "acme", "Acme")
    await seed_membership(migrations_pool, user_a, acme_id)

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)

    for i in range(3):
        files = {"file": (f"doc-{i}.txt", f"doc {i}".encode(), "text/plain")}
        response = await app_client.post("/api/orgs/acme/documents", files=files)
        assert response.status_code == 201

    listing = await app_client.get("/api/orgs/acme/documents")
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 3
    # Newest first.
    assert rows[0]["title"] == "doc-2.txt"

    page2 = await app_client.get(
        "/api/orgs/acme/documents",
        params={"limit": 1, "offset": 1},
    )
    assert page2.status_code == 200
    assert len(page2.json()) == 1
    assert page2.json()[0]["title"] == "doc-1.txt"


async def test_oversized_upload_returns_413(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upload exceeding ``max_upload_bytes`` is rejected with 413."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await seed_org(migrations_pool, "acme", "Acme")
    await seed_membership(migrations_pool, user_a, acme_id)

    monkeypatch.setenv("UNSTASH_MAX_UPLOAD_BYTES", "8")
    get_settings.cache_clear()

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    files = {"file": ("oversized.txt", b"this is too long", "text/plain")}
    response = await app_client.post("/api/orgs/acme/documents", files=files)
    assert response.status_code == 413


async def test_cross_org_isolation_on_documents_routes(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """User B in Beta cannot see User A's Acme document by id or listing."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    user_b = await seed_user(migrations_pool, USER_B_EMAIL, USER_PASSWORD)
    acme_id = await seed_org(migrations_pool, "acme", "Acme")
    beta_id = await seed_org(migrations_pool, "beta", "Beta")
    await seed_membership(migrations_pool, user_a, acme_id)
    await seed_membership(migrations_pool, user_b, beta_id)

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    files = {"file": ("acme-doc.txt", b"acme-only", "text/plain")}
    response = await app_client.post("/api/orgs/acme/documents", files=files)
    assert response.status_code == 201
    acme_document_id = response.json()["document_id"]

    await app_client.post("/api/auth/logout")
    app_client.cookies.clear()
    await login(app_client, USER_B_EMAIL, USER_PASSWORD)

    # User B is in Beta, not Acme: listing Acme is 403.
    cross_listing = await app_client.get("/api/orgs/acme/documents")
    assert cross_listing.status_code == 403

    # Beta's own listing is empty.
    own_listing = await app_client.get("/api/orgs/beta/documents")
    assert own_listing.status_code == 200
    assert own_listing.json() == []

    # If User B authenticated as the *url* points at Beta but the document_id
    # is Acme's, the GET returns 404 — RLS hides Acme's row from Beta's
    # context. (The dependency itself doesn't 403 because the slug is Beta,
    # and User B is a member of Beta.)
    cross_get = await app_client.get(f"/api/orgs/beta/documents/{acme_document_id}")
    assert cross_get.status_code == 404


async def test_duplicate_upload_returns_existing_document(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """The same bytes uploaded twice in one org dedupe to the first document."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await seed_org(migrations_pool, "acme", "Acme")
    await seed_membership(migrations_pool, user_a, acme_id)
    await login(app_client, USER_A_EMAIL, USER_PASSWORD)

    payload = b"identical bytes uploaded twice\n"
    files = {"file": ("original.txt", payload, "text/plain")}
    first = await app_client.post("/api/orgs/acme/documents", files=files)
    assert first.status_code == 201
    first_id = first.json()["document_id"]

    files = {"file": ("copy-of-original.txt", payload, "text/plain")}
    second = await app_client.post("/api/orgs/acme/documents", files=files)
    assert second.status_code == 200
    body = second.json()
    assert body["duplicate"] is True
    assert body["document_id"] == first_id
    assert body["job_id"] is None

    # Only the original row exists for that content hash.
    async with migrations_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE org_id = $1",
            acme_id,
        )
    assert count == 1

    await _wait_terminal(app_client, "acme", first_id)


async def test_same_content_in_different_orgs_is_not_deduped(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Dedup is per-org: identical bytes in two orgs ingest independently."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    user_b = await seed_user(migrations_pool, USER_B_EMAIL, USER_PASSWORD)
    acme_id = await seed_org(migrations_pool, "acme", "Acme")
    beta_id = await seed_org(migrations_pool, "beta", "Beta")
    await seed_membership(migrations_pool, user_a, acme_id)
    await seed_membership(migrations_pool, user_b, beta_id)

    payload = b"shared bytes across orgs\n"

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    first = await app_client.post(
        "/api/orgs/acme/documents",
        files={"file": ("doc.txt", payload, "text/plain")},
    )
    assert first.status_code == 201

    await app_client.post("/api/auth/logout")
    app_client.cookies.clear()
    await login(app_client, USER_B_EMAIL, USER_PASSWORD)
    second = await app_client.post(
        "/api/orgs/beta/documents",
        files={"file": ("doc.txt", payload, "text/plain")},
    )
    assert second.status_code == 201
    assert second.json()["duplicate"] is False

    await _wait_terminal(app_client, "beta", second.json()["document_id"])
    await app_client.post("/api/auth/logout")
    app_client.cookies.clear()
    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    await _wait_terminal(app_client, "acme", first.json()["document_id"])


async def test_failed_document_does_not_block_reupload(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Re-uploading bytes whose previous ingestion failed starts a new attempt."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await seed_org(migrations_pool, "acme", "Acme")
    await seed_membership(migrations_pool, user_a, acme_id)
    await login(app_client, USER_A_EMAIL, USER_PASSWORD)

    # ELF magic bytes route to SKIP and the document lands in failed.
    payload = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64
    first = await app_client.post(
        "/api/orgs/acme/documents",
        files={"file": ("blob.bin", payload, "application/octet-stream")},
    )
    assert first.status_code == 201
    first_id = first.json()["document_id"]

    for _ in range(600):
        doc = await app_client.get(f"/api/orgs/acme/documents/{first_id}")
        if doc.json()["status"] == "failed":
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("first upload never reached failed")

    second = await app_client.post(
        "/api/orgs/acme/documents",
        files={"file": ("blob.bin", payload, "application/octet-stream")},
    )
    assert second.status_code == 201
    assert second.json()["duplicate"] is False
    assert second.json()["document_id"] != first_id

    assert await _wait_terminal(app_client, "acme", second.json()["document_id"]) == "failed"


async def test_daily_upload_limit_enforced(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Uploads beyond the org's daily limit are rejected with 429.

    Duplicates do not consume quota (no row is created), and an org
    with a NULL limit is unaffected (every other test exercises that).
    """
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await seed_org(migrations_pool, "acme", "Acme")
    await seed_membership(migrations_pool, user_a, acme_id)
    async with migrations_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organisations SET daily_upload_limit = 2 WHERE id = $1",
            acme_id,
        )
    await login(app_client, USER_A_EMAIL, USER_PASSWORD)

    first = await app_client.post(
        "/api/orgs/acme/documents",
        files={"file": ("one.txt", b"first payload", "text/plain")},
    )
    assert first.status_code == 201

    # A duplicate of the first upload: 200, creates no row, no quota use.
    dup = await app_client.post(
        "/api/orgs/acme/documents",
        files={"file": ("one-again.txt", b"first payload", "text/plain")},
    )
    assert dup.status_code == 200

    second = await app_client.post(
        "/api/orgs/acme/documents",
        files={"file": ("two.txt", b"second payload", "text/plain")},
    )
    assert second.status_code == 201

    third = await app_client.post(
        "/api/orgs/acme/documents",
        files={"file": ("three.txt", b"third payload", "text/plain")},
    )
    assert third.status_code == 429
    assert "limit" in third.json()["detail"].lower()

    await _wait_terminal(app_client, "acme", first.json()["document_id"])
    await _wait_terminal(app_client, "acme", second.json()["document_id"])
