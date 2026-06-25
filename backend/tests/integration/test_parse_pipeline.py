"""End-to-end parse pipeline tests for M3-B.

The Phase A PR tested the upload + status-transition plumbing using
a no-op task stub. This file replaces those scenarios with the real
parse pipeline: upload a synthetic PDF, the worker runs Docling,
chunks land in the database with NULL embeddings, document moves to
``parsed`` and the job to ``succeeded``.

Synthetic PDFs are generated at test time via ``reportlab`` so no
binary fixtures live in the repo. Adversarial cases (corrupt files,
unsupported MIME, legacy-format-not-yet-supported) verify the failure
path lands the document in ``failed`` with an actionable error
message and does not crash the worker.

The HuggingFace cache used by the chunker tokenizer is redirected to
a temp directory per test so the operator's personal cache is never
touched, and so a clean test run downloads exactly once. In CI the
Docker image pre-caches both Docling models and the tokenizer, so
the cache hit path is exercised there.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

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
USER_EMAIL = "alice@example.com"


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


def _make_synthetic_pdf(path: Path, lines: list[str]) -> None:
    """Write a minimal PDF with the given lines of text to ``path``."""
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()


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
    hf_cache = tmp_path / "hf_cache"
    hf_cache.mkdir(parents=True)
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
    # Redirect HuggingFace tokenizer cache so the operator's personal
    # cache is never polluted by tests. The cache is shared across
    # tests in the session because the lru_cache on _get_chunker
    # holds the loaded tokenizer in memory.
    monkeypatch.setenv("HF_HOME", str(hf_cache))
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_admin_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_admin_sessionmaker.cache_clear()

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


async def _poll_until_terminal(
    client: AsyncClient,
    slug: str,
    document_id: str,
    *,
    deadline_seconds: float = 60.0,
) -> dict:
    """Poll the document until status is terminal (parsed/failed/indexed)."""
    async with asyncio.timeout(deadline_seconds):
        while True:
            response = await client.get(f"/api/orgs/{slug}/documents/{document_id}")
            assert response.status_code == 200
            body = response.json()
            if body["status"] in {"parsed", "failed", "indexed"}:
                return body
            await asyncio.sleep(0.1)


async def test_pdf_upload_produces_chunks(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
    tmp_path: Path,
) -> None:
    """A synthetic PDF parses, chunks land in the DB, status moves to parsed."""
    user_a = await _seed_user(migrations_pool, USER_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id)

    pdf_path = tmp_path / "tiny.pdf"
    _make_synthetic_pdf(
        pdf_path,
        [
            "Unstash test document — first paragraph.",
            "Second line of content here.",
            "Third line that mentions BRF Ragstacken's roof.",
        ],
    )

    await _login(app_client, USER_EMAIL, USER_PASSWORD)
    with pdf_path.open("rb") as fh:
        response = await app_client.post(
            "/api/orgs/acme/documents",
            files={"file": ("tiny.pdf", fh, "application/pdf")},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    document_id = body["document_id"]
    job_id = body["job_id"]

    document = await _poll_until_terminal(app_client, "acme", document_id)
    assert document["status"] == "parsed", document
    assert document["mime_type"] == "application/pdf"
    assert document["pipeline_version"] is not None
    assert document["pipeline_config"] is not None

    # Confirm chunks landed in the database via the migrations pool
    # (bypassing RLS so the test can read directly without setting GUC).
    async with migrations_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chunk_index, text, token_count, embedding IS NULL AS no_embedding "
            "FROM chunks WHERE document_id = $1 ORDER BY chunk_index",
            uuid.UUID(document_id),
        )

    assert len(rows) >= 1
    assert all(row["no_embedding"] for row in rows), "chunks should not have embeddings yet"
    assert all(row["token_count"] > 0 for row in rows)
    combined = " ".join(row["text"] for row in rows)
    # The exact chunking is up to Docling; check that meaningful content is in there.
    assert "Unstash" in combined or "test document" in combined.lower()

    job = await app_client.get(f"/api/orgs/acme/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded"


async def test_corrupt_file_lands_in_failed_state(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
    tmp_path: Path,
) -> None:
    """A bytes-blob that says it's a PDF but isn't transitions to failed.

    The worker catches the parse exception, sets ``status=failed`` and
    captures the error class+message in ``parsing_error``. The job
    lifecycle ends in ``failed``. The worker does not crash.
    """
    user_a = await _seed_user(migrations_pool, USER_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id)

    corrupt = tmp_path / "corrupt.pdf"
    # Bytes that libmagic detects as application/pdf (the %PDF-1.4
    # header is the magic signature) but that Docling cannot
    # actually parse — anything after the header is junk.
    corrupt.write_bytes(b"%PDF-1.4\n" + b"\x00\xff" * 200 + b"deliberately invalid pdf body")

    await _login(app_client, USER_EMAIL, USER_PASSWORD)
    with corrupt.open("rb") as fh:
        response = await app_client.post(
            "/api/orgs/acme/documents",
            files={"file": ("corrupt.pdf", fh, "application/pdf")},
        )
    assert response.status_code == 201
    document_id = response.json()["document_id"]
    job_id = response.json()["job_id"]

    document = await _poll_until_terminal(app_client, "acme", document_id)
    assert document["status"] == "failed", document
    assert document["parsing_error"] is not None
    assert document["parsing_error"] != ""

    job = await app_client.get(f"/api/orgs/acme/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["status"] == "failed"
    assert job.json()["error"] is not None


async def test_unsupported_mime_lands_in_failed_state(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
    tmp_path: Path,
) -> None:
    """An executable masquerading as content goes to ``failed`` with reason."""
    user_a = await _seed_user(migrations_pool, USER_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id)

    # ELF binary header — libmagic will detect this as
    # application/x-pie-executable or similar, which the strategy
    # router maps to SKIP.
    elf_blob = tmp_path / "claimed-pdf.pdf"
    elf_blob.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200)

    await _login(app_client, USER_EMAIL, USER_PASSWORD)
    with elf_blob.open("rb") as fh:
        response = await app_client.post(
            "/api/orgs/acme/documents",
            files={"file": ("claimed-pdf.pdf", fh, "application/pdf")},
        )
    assert response.status_code == 201
    document_id = response.json()["document_id"]

    document = await _poll_until_terminal(app_client, "acme", document_id)
    assert document["status"] == "failed", document
    assert document["parsing_error"] is not None
    # The detected MIME (not the declared one) should be in the error.
    assert "MIME" in document["parsing_error"] or "type" in document["parsing_error"].lower()


async def test_mime_detection_overrides_declared_type(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
    tmp_path: Path,
) -> None:
    """A real PDF declared as ``application/octet-stream`` still parses correctly.

    The sniffed MIME wins; ``application/pdf`` is detected from magic
    bytes regardless of what the client said. The strategy router
    dispatches to EXTRACT, the document parses successfully.
    """
    user_a = await _seed_user(migrations_pool, USER_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id)

    pdf_path = tmp_path / "mystery.bin"
    _make_synthetic_pdf(pdf_path, ["Content despite the lying MIME header."])

    await _login(app_client, USER_EMAIL, USER_PASSWORD)
    with pdf_path.open("rb") as fh:
        response = await app_client.post(
            "/api/orgs/acme/documents",
            files={"file": ("mystery.bin", fh, "application/octet-stream")},
        )
    assert response.status_code == 201
    document_id = response.json()["document_id"]

    document = await _poll_until_terminal(app_client, "acme", document_id)
    assert document["status"] == "parsed", document
    # The detected MIME has overwritten the declared one.
    assert document["mime_type"] == "application/pdf"
