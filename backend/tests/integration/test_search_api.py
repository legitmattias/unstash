"""End-to-end tests for the search routes.

Runs the full pipeline against real Postgres (vector + BM25 indexes at
migration head) with the deterministic fake embedder and reranker:
upload → index → search finds the document → search_logs row written →
click recorded. Cross-org isolation is exercised adversarially: search
and click attribution must never cross tenants.
"""

from __future__ import annotations

import json
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
from unstash.documents.embedder import EmbeddingTask, FakeEmbedder
from unstash.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

USER_PASSWORD = uuid.uuid4().hex + "Aa1"
USER_A_EMAIL = "searcher-a@example.com"
USER_B_EMAIL = "searcher-b@example.com"


async def _seed_indexed_document(  # noqa: PLR0913 — seed knobs for filter tests
    pool: asyncpg.Pool,
    org_id: uuid.UUID,
    title: str,
    chunk_texts: list[str],
    status: str = "indexed",
    mime_type: str = "text/markdown",
    dates: list[str] | None = None,
) -> uuid.UUID:
    """Insert a document with embedded chunks, defaulting to ``indexed``.

    ``status``, ``mime_type`` and extracted ``dates`` (ISO strings, written
    to ``document_metadata``) are overridable so filter tests can seed
    documents that differ on exactly the filtered dimension.
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
            mime_type,
            100,
            f"hash-{title}-{org_id}",
            status,
        )
        if dates is not None:
            await conn.execute(
                "INSERT INTO document_metadata (org_id, document_id, dates, extractor_version) "
                "VALUES ($1, $2, $3::jsonb, 'test')",
                org_id,
                document_id,
                json.dumps([{"raw": iso, "iso": iso} for iso in dates]),
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


async def test_search_finds_seeded_document(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """The planted fact comes back, reranked, with a search log row."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await seed_org(migrations_pool, "org-a", "Org A")
    await seed_membership(migrations_pool, user_a, org_a)
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

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "takrenoveringen beslutade"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["result_count"] >= 1
    assert body["results"][0]["document_id"] == str(roof_doc)
    assert "takrenoveringen" in body["results"][0]["excerpt"]
    assert body["results"][0]["snippet"]  # bounded excerpt present
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

    results = json.loads(log["results"])
    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))
    assert results[0]["document_id"] == str(roof_doc)
    config = json.loads(log["ranking_config"])
    assert config["reranked"] is True
    assert config["bm25_used"] is True
    assert config["rrf_k"] == get_settings().search_rrf_k


async def test_search_empty_result_shape(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """No indexed content yields an explicit empty response, still logged."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await seed_org(migrations_pool, "org-a", "Org A")
    await seed_membership(migrations_pool, user_a, org_a)

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "finns det något här"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result_count"] == 0
    assert body["results"] == []


async def test_mime_type_filter_restricts_results(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A mime_type filter returns only documents of that type."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await seed_org(migrations_pool, "org-a", "Org A")
    await seed_membership(migrations_pool, user_a, org_a)
    text = ["Styrelsen beslutade om takrenovering och budget."]
    pdf_doc = await _seed_indexed_document(
        migrations_pool,
        org_a,
        "protokoll.pdf",
        text,
        mime_type="application/pdf",
    )
    await _seed_indexed_document(
        migrations_pool,
        org_a,
        "notes.md",
        text,
        mime_type="text/markdown",
    )

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "takrenovering budget", "mime_type": "application/pdf"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    ids = [r["document_id"] for r in body["results"]]
    assert ids == [str(pdf_doc)]


async def test_date_range_filter_restricts_results(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A date range returns only documents with an extracted date in range."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await seed_org(migrations_pool, "org-a", "Org A")
    await seed_membership(migrations_pool, user_a, org_a)
    text = ["Föreningens ekonomi och budget för året."]
    doc_2022 = await _seed_indexed_document(
        migrations_pool,
        org_a,
        "budget-2022.md",
        text,
        dates=["2022-03-15"],
    )
    await _seed_indexed_document(
        migrations_pool,
        org_a,
        "budget-2023.md",
        text,
        dates=["2023-06-01"],
    )

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get(
        "/api/orgs/org-a/search",
        params={
            "q": "ekonomi budget",
            "date_from": "2022-01-01",
            "date_to": "2022-12-31",
        },
    )
    assert response.status_code == 200, response.text
    ids = [r["document_id"] for r in response.json()["results"]]
    assert ids == [str(doc_2022)]


async def test_failed_document_with_embedded_chunks_is_not_searchable(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A failed document keeps its committed chunks but must not surface."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await seed_org(migrations_pool, "org-a", "Org A")
    await seed_membership(migrations_pool, user_a, org_a)
    await _seed_indexed_document(
        migrations_pool,
        org_a,
        "halvklart-protokoll.md",
        ["Styrelsen beslutade att byta ut porttelefonen i entrén."],
        status="failed",
    )

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
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
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await seed_org(migrations_pool, "org-a", "Org A")
    await seed_membership(migrations_pool, user_a, org_a)
    doc = await _seed_indexed_document(
        migrations_pool,
        org_a,
        "avtal-el.md",
        ["Avtal om elleverans till föreningens fastigheter."],
    )

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
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
        row = await conn.fetchrow(
            "SELECT clicked_document_id, clicks FROM search_logs WHERE id = $1",
            uuid.UUID(search_id),
        )
    assert row["clicked_document_id"] == doc
    clicks = json.loads(row["clicks"])
    assert len(clicks) == 1
    assert clicks[0]["document_id"] == str(doc)
    assert clicks[0]["position"] == 1
    assert clicks[0]["clicked_at"]


async def test_multiple_clicks_accumulate_without_overwriting(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A second click is recorded alongside the first, not on top of it."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    org_a = await seed_org(migrations_pool, "org-a", "Org A")
    await seed_membership(migrations_pool, user_a, org_a)
    doc_one = await _seed_indexed_document(
        migrations_pool,
        org_a,
        "avtal-el.md",
        ["Avtal om elleverans till föreningens fastigheter."],
    )
    doc_two = await _seed_indexed_document(
        migrations_pool,
        org_a,
        "avtal-varme.md",
        ["Avtal om fjärrvärme och uppvärmning av fastigheten."],
    )

    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    search = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "avtal"},
    )
    search_id = search.json()["search_id"]

    for doc in (doc_one, doc_two):
        click = await app_client.post(
            f"/api/orgs/org-a/search/{search_id}/click",
            json={"document_id": str(doc)},
        )
        assert click.status_code == 200, click.text

    async with migrations_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT clicked_document_id, clicks FROM search_logs WHERE id = $1",
            uuid.UUID(search_id),
        )
    clicks = json.loads(row["clicks"])
    assert [c["document_id"] for c in clicks] == [str(doc_one), str(doc_two)]
    # clicked_document_id tracks the latest.
    assert row["clicked_document_id"] == doc_two


async def test_search_never_returns_other_org_content(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Adversarial: org B's user cannot surface org A's chunks via search."""
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    user_b = await seed_user(migrations_pool, USER_B_EMAIL, USER_PASSWORD)
    org_a = await seed_org(migrations_pool, "org-a", "Org A")
    org_b = await seed_org(migrations_pool, "org-b", "Org B")
    await seed_membership(migrations_pool, user_a, org_a)
    await seed_membership(migrations_pool, user_b, org_b)
    await _seed_indexed_document(
        migrations_pool,
        org_a,
        "hemligt-protokoll.md",
        ["Konfidentiellt styrelsebeslut om fastighetsförsäljningen."],
    )

    await login(app_client, USER_B_EMAIL, USER_PASSWORD)

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
    user_a = await seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    user_b = await seed_user(migrations_pool, USER_B_EMAIL, USER_PASSWORD)
    org_a = await seed_org(migrations_pool, "org-a", "Org A")
    org_b = await seed_org(migrations_pool, "org-b", "Org B")
    await seed_membership(migrations_pool, user_a, org_a)
    await seed_membership(migrations_pool, user_b, org_b)
    doc_a = await _seed_indexed_document(
        migrations_pool,
        org_a,
        "protokoll.md",
        ["Beslut om ny tvättstuga."],
    )

    # Org A runs a legitimate search; org B tries to patch its log row.
    await login(app_client, USER_A_EMAIL, USER_PASSWORD)
    search = await app_client.get(
        "/api/orgs/org-a/search",
        params={"q": "tvättstuga"},
    )
    search_id = search.json()["search_id"]

    await login(app_client, USER_B_EMAIL, USER_PASSWORD)

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
