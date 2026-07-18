"""End-to-end tests for the search routes.

Runs the full pipeline against real Postgres (vector + BM25 indexes at
migration head) with the deterministic fake embedder and reranker:
upload → index → search finds the document → search_logs row written →
click recorded. Cross-org isolation is exercised adversarially: search
and click attribution must never cross tenants.
"""

from __future__ import annotations

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
from unstash.documents.embedder import EmbeddingTask, FakeEmbedder
from unstash.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

USER_PASSWORD = uuid.uuid4().hex + "Aa1"
USER_A_EMAIL = "searcher-a@example.com"
USER_B_EMAIL = "searcher-b@example.com"


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
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO org_memberships (user_id, org_id, role) VALUES ($1, $2, $3)",
            user_id,
            org_id,
            "member",
        )


async def _seed_indexed_document(
    pool: asyncpg.Pool,
    org_id: uuid.UUID,
    title: str,
    chunk_texts: list[str],
    status: str = "indexed",
) -> uuid.UUID:
    """Insert a document with embedded chunks, defaulting to ``indexed``.

    ``status`` is overridable so tests can seed a ``failed`` document that
    still carries embedded chunks (the partial-progress case).
    """
    embedder = FakeEmbedder(dimensions=get_settings().jina_embedding_dimensions)
    batch = await embedder.embed(chunk_texts, task=EmbeddingTask.PASSAGE)
    async with pool.acquire() as conn:
        document_id = await conn.fetchval(
            "INSERT INTO documents "
            "(org_id, title, source_uri, mime_type, size_bytes, content_hash, status) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
            org_id,
            title,
            f"/seed/{title}",
            "text/markdown",
            100,
            f"hash-{title}-{org_id}",
            status,
        )
        for index, (chunk_text, vector) in enumerate(
            zip(chunk_texts, batch.vectors, strict=True),
        ):
            await conn.execute(
                "INSERT INTO chunks "
                "(org_id, document_id, chunk_index, text, token_count, "
                " char_offset_start, char_offset_end, embedding) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)",
                org_id,
                document_id,
                index,
                chunk_text,
                len(chunk_text) // 4,
                0,
                len(chunk_text),
                "[" + ",".join(str(v) for v in vector) + "]",
            )
    return document_id


@pytest.fixture
async def app_client(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> AsyncIterator[AsyncClient]:
    """ASGI client with fake embedder and fake reranker backends."""
    _ = migrated_database
    host, port = container_host_port
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
    monkeypatch.setenv("UNSTASH_EMBEDDER_BACKEND", "fake")
    monkeypatch.setenv("UNSTASH_RERANKER_BACKEND", "fake")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_admin_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_admin_sessionmaker.cache_clear()

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        await dispose_engine()


async def _login(client: AsyncClient, email: str, password: str) -> None:
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )
    assert response.status_code == 204, response.text


async def test_search_finds_seeded_document(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """The planted fact comes back, reranked, with a search log row."""
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await _seed_org(migrations_pool, "org-a", "Org A")
    await _seed_membership(migrations_pool, user_a, org_a)
    roof_doc = await _seed_indexed_document(
        migrations_pool,
        org_a,
        "styrelseprotokoll-2026-03.md",
        [
            "Styrelsen beslutade att anlita en entreprenör för takrenoveringen.",
            "Mötet avslutades klockan nitton.",
        ],
    )
    await _seed_indexed_document(
        migrations_pool,
        org_a,
        "arsredovisning-2025.md",
        ["Föreningens ekonomi är god och budgeten i balans."],
    )

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "takrenoveringen beslutade"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["result_count"] >= 1
    assert body["results"][0]["document_id"] == str(roof_doc)
    assert "takrenoveringen" in body["results"][0]["excerpt"]
    assert body["reranked"] is True
    assert body["bm25_used"] is True
    assert body["latency_ms"] >= 0

    async with migrations_pool.acquire() as conn:
        log = await conn.fetchrow(
            "SELECT * FROM search_logs WHERE id = $1",
            uuid.UUID(body["search_id"]),
        )
    assert log is not None
    assert log["org_id"] == org_a
    assert log["query"] == "takrenoveringen beslutade"
    assert log["result_count"] == body["result_count"]
    assert log["top_document_id"] == roof_doc


async def test_search_empty_result_shape(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """No indexed content yields an explicit empty response, still logged."""
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await _seed_org(migrations_pool, "org-a", "Org A")
    await _seed_membership(migrations_pool, user_a, org_a)

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "finns det något här"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result_count"] == 0
    assert body["results"] == []


async def test_failed_document_with_embedded_chunks_is_not_searchable(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A failed document keeps its committed chunks but must not surface."""
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await _seed_org(migrations_pool, "org-a", "Org A")
    await _seed_membership(migrations_pool, user_a, org_a)
    await _seed_indexed_document(
        migrations_pool,
        org_a,
        "halvklart-protokoll.md",
        ["Styrelsen beslutade att byta ut porttelefonen i entrén."],
        status="failed",
    )

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "porttelefonen i entrén"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["result_count"] == 0


async def test_click_reporting_round_trip(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A reported click lands in the search log row."""
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await _seed_org(migrations_pool, "org-a", "Org A")
    await _seed_membership(migrations_pool, user_a, org_a)
    doc = await _seed_indexed_document(
        migrations_pool,
        org_a,
        "avtal-el.md",
        ["Avtal om elleverans till föreningens fastigheter."],
    )

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    search = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "avtal elleverans"},
    )
    search_id = search.json()["search_id"]

    click = await app_client.post(
        f"/api/orgs/org-a/search/{search_id}/click",
        json={"document_id": str(doc)},
    )
    assert click.status_code == 200, click.text

    async with migrations_pool.acquire() as conn:
        clicked = await conn.fetchval(
            "SELECT clicked_document_id FROM search_logs WHERE id = $1",
            uuid.UUID(search_id),
        )
    assert clicked == doc


async def test_search_never_returns_other_org_content(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Adversarial: org B's user cannot surface org A's chunks via search."""
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    user_b = await _seed_user(migrations_pool, USER_B_EMAIL, USER_PASSWORD)
    org_a = await _seed_org(migrations_pool, "org-a", "Org A")
    org_b = await _seed_org(migrations_pool, "org-b", "Org B")
    await _seed_membership(migrations_pool, user_a, org_a)
    await _seed_membership(migrations_pool, user_b, org_b)
    await _seed_indexed_document(
        migrations_pool,
        org_a,
        "hemligt-protokoll.md",
        ["Konfidentiellt styrelsebeslut om fastighetsförsäljningen."],
    )

    await _login(app_client, USER_B_EMAIL, USER_PASSWORD)

    # Exact content match against the other org: nothing may come back.
    response = await app_client.get(
        "/api/orgs/org-b/search",
        params={"q": "Konfidentiellt styrelsebeslut om fastighetsförsäljningen."},
    )
    assert response.status_code == 200
    assert response.json()["result_count"] == 0

    # Org B's user cannot query org A's slug at all.
    forbidden = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "vad som helst"},
    )
    assert forbidden.status_code == 403


async def test_click_cannot_cross_orgs(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Adversarial: click attribution cannot reference another org's data."""
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    user_b = await _seed_user(migrations_pool, USER_B_EMAIL, USER_PASSWORD)
    org_a = await _seed_org(migrations_pool, "org-a", "Org A")
    org_b = await _seed_org(migrations_pool, "org-b", "Org B")
    await _seed_membership(migrations_pool, user_a, org_a)
    await _seed_membership(migrations_pool, user_b, org_b)
    doc_a = await _seed_indexed_document(
        migrations_pool,
        org_a,
        "protokoll.md",
        ["Beslut om ny tvättstuga."],
    )

    # Org A runs a legitimate search; org B tries to patch its log row.
    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    search = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "tvättstuga"},
    )
    search_id = search.json()["search_id"]

    await _login(app_client, USER_B_EMAIL, USER_PASSWORD)

    # Org B cannot click-attribute org A's search via org B's slug:
    # the document lookup is org-scoped, so org A's doc is invisible.
    cross_doc = await app_client.post(
        f"/api/orgs/org-b/search/{search_id}/click",
        json={"document_id": str(doc_a)},
    )
    assert cross_doc.status_code == 404

    async with migrations_pool.acquire() as conn:
        clicked = await conn.fetchval(
            "SELECT clicked_document_id FROM search_logs WHERE id = $1",
            uuid.UUID(search_id),
        )
    assert clicked is None
