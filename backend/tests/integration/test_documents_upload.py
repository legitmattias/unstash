"""End-to-end upload + lifecycle tests for the documents routes.

Exercises the full Phase A loop: POST upload streams a file to disk,
inserts pending document and queued job rows, queues the stub task;
the in-memory Taskiq broker runs the task body; document and job
status transition to ``parsed`` and ``succeeded``; the monitoring
routes surface the final state.

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
)
from unstash.auth.manager import _password_helper
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


async def _seed_user(pool: asyncpg.Pool, email: str, password: str) -> uuid.UUID:
    hashed = _password_helper().hash(password)
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO users (email, hashed_password, is_active, is_verified) "
            "VALUES ($1, $2, true, true) RETURNING id",
            email,
            hashed,
        )


async def _seed_org(pool: asyncpg.Pool, slug: str, name: str) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO organisations (slug, name) VALUES ($1, $2) RETURNING id",
            slug,
            name,
        )


async def _seed_membership(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    role: str = "member",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO org_memberships (user_id, org_id, role) VALUES ($1, $2, $3)",
            user_id,
            org_id,
            role,
        )


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


async def _login(client: AsyncClient, email: str, password: str) -> None:
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )
    assert response.status_code == 204, response.text


async def test_upload_round_trip(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Small file uploads, queues a job, transitions through the stub, lands in monitoring."""
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id)

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)

    payload = b"hello world, this is a tiny test document\n"
    files = {"file": ("hello.txt", payload, "text/plain")}
    response = await app_client.post("/api/orgs/acme/documents", files=files)
    assert response.status_code == 201, response.text
    body = response.json()
    document_id = body["document_id"]
    job_id = body["job_id"]

    # The stub task ran inline via the InMemoryBroker before kiq()
    # returned (await_inplace=False is the default — the test relies
    # on either the broker dispatching synchronously or on a poll).
    # We poll the monitoring routes briefly to catch the transition.
    for _ in range(20):
        doc = await app_client.get(f"/api/orgs/acme/documents/{document_id}")
        assert doc.status_code == 200
        if doc.json()["status"] == "parsed":
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail(f"document never reached parsed, last body: {doc.json()}")

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
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id)

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)

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


async def test_list_documents_paginates(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """``GET /documents`` returns newest first with limit/offset semantics."""
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id)

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)

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
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id)

    monkeypatch.setenv("UNSTASH_MAX_UPLOAD_BYTES", "8")
    get_settings.cache_clear()

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    files = {"file": ("oversized.txt", b"this is too long", "text/plain")}
    response = await app_client.post("/api/orgs/acme/documents", files=files)
    assert response.status_code == 413


async def test_cross_org_isolation_on_documents_routes(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """User B in Beta cannot see User A's Acme document by id or listing."""
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    user_b = await _seed_user(migrations_pool, USER_B_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    beta_id = await _seed_org(migrations_pool, "beta", "Beta")
    await _seed_membership(migrations_pool, user_a, acme_id)
    await _seed_membership(migrations_pool, user_b, beta_id)

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    files = {"file": ("acme-doc.txt", b"acme-only", "text/plain")}
    response = await app_client.post("/api/orgs/acme/documents", files=files)
    assert response.status_code == 201
    acme_document_id = response.json()["document_id"]

    await app_client.post("/api/auth/logout")
    app_client.cookies.clear()
    await _login(app_client, USER_B_EMAIL, USER_PASSWORD)

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
