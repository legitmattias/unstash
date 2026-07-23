"""End-to-end tests for the clusters routes and the search category filter.

Seeds clustering runs and clusters directly (the pipeline itself is covered
by test_clustering_service.py), then exercises the API surface: listing the
latest run, the manual re-cluster action with its in-flight guard, the
category filter on search, and cross-org isolation.
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


async def _seed_run_with_clusters(
    pool: asyncpg.Pool,
    org_id: uuid.UUID,
    labels: list[str],
    status: str = "succeeded",
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Insert a clustering run and one cluster per label."""
    async with pool.acquire() as conn:
        run_id = await conn.fetchval(
            "INSERT INTO clustering_runs "
            "(org_id, status, triggered_by, document_count, cluster_count, "
            " noise_count, silhouette, params) "
            "VALUES ($1, $2, 'manual', 45, $3, 2, 0.61, '{}'::jsonb) RETURNING id",
            org_id,
            status,
            len(labels),
        )
        cluster_ids = []
        for label in labels:
            cluster_ids.append(
                await conn.fetchval(
                    "INSERT INTO clusters "
                    "(org_id, run_id, label, keywords, representative_document_ids, size) "
                    "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, 10) RETURNING id",
                    org_id,
                    run_id,
                    label,
                    json.dumps([{"term": label.split(",")[0], "score": 0.9}]),
                    json.dumps([]),
                ),
            )
    return run_id, cluster_ids


async def _seed_document_in_cluster(
    pool: asyncpg.Pool,
    org_id: uuid.UUID,
    title: str,
    chunk_text: str,
    cluster_id: uuid.UUID | None,
) -> uuid.UUID:
    """Insert an indexed document with one embedded chunk, assigned to a cluster."""
    embedder = FakeEmbedder(dimensions=get_settings().jina_embedding_dimensions)
    batch = await embedder.embed([chunk_text], task=EmbeddingTask.PASSAGE)
    async with pool.acquire() as conn:
        document_id = await conn.fetchval(
            "INSERT INTO documents "
            "(org_id, title, source_uri, mime_type, size_bytes, content_hash, "
            " status, cluster_id) "
            "VALUES ($1, $2, $3, 'text/markdown', 100, $4, 'indexed', $5) RETURNING id",
            org_id,
            title,
            f"/seed/{title}",
            f"hash-{title}-{org_id}",
            cluster_id,
        )
        await conn.execute(
            "INSERT INTO chunks "
            "(org_id, document_id, chunk_index, text, token_count, "
            " char_offset_start, char_offset_end, embedding) "
            "VALUES ($1, $2, 0, $3, $4, 0, $5, $6::vector)",
            org_id,
            document_id,
            chunk_text,
            len(chunk_text) // 4,
            len(chunk_text),
            "[" + ",".join(str(v) for v in batch.vectors[0]) + "]",
        )
    return document_id


async def _seed_member(
    pool: asyncpg.Pool,
    slug: str,
) -> tuple[uuid.UUID, str, str]:
    """Seed an org and a member; return (org_id, slug, member email)."""
    suffix = uuid.uuid4().hex[:8]
    email = f"member-{suffix}@example.com"
    user_id = await seed_user(pool, email, USER_PASSWORD)
    org_id = await seed_org(pool, f"{slug}-{suffix}", f"Org {suffix}")
    await seed_membership(pool, user_id, org_id)
    return org_id, f"{slug}-{suffix}", email


async def test_clusters_empty_before_first_run(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    _, slug, email = await _seed_member(migrations_pool, "empty")

    await login(app_client, email, USER_PASSWORD)
    response = await app_client.get(f"/api/orgs/{slug}/clusters")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] is None
    assert body["clusters"] == []


async def test_clusters_list_latest_succeeded_run(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id, slug, email = await _seed_member(migrations_pool, "list")
    run_id, cluster_ids = await _seed_run_with_clusters(
        migrations_pool,
        org_id,
        ["protokoll, styrelse", "faktura, betalning"],
    )

    await login(app_client, email, USER_PASSWORD)
    response = await app_client.get(f"/api/orgs/{slug}/clusters")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == str(run_id)
    assert body["document_count"] == 45
    assert body["silhouette"] == pytest.approx(0.61)
    assert {c["id"] for c in body["clusters"]} == {str(c) for c in cluster_ids}
    labels = {c["label"] for c in body["clusters"]}
    assert labels == {"protokoll, styrelse", "faktura, betalning"}
    for cluster in body["clusters"]:
        assert cluster["label_source"] == "keyword_fallback"
        assert cluster["keywords"][0]["term"]


async def test_category_filter_restricts_and_labels_results(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id, slug, email = await _seed_member(migrations_pool, "filter")
    _, cluster_ids = await _seed_run_with_clusters(
        migrations_pool,
        org_id,
        ["protokoll", "faktura"],
    )
    protokoll_doc = await _seed_document_in_cluster(
        migrations_pool,
        org_id,
        "styrelseprotokoll.md",
        "Styrelsen beslutade om takrenovering i protokollet.",
        cluster_ids[0],
    )
    await _seed_document_in_cluster(
        migrations_pool,
        org_id,
        "faktura-tak.md",
        "Faktura för takrenovering från entreprenören.",
        cluster_ids[1],
    )

    await login(app_client, email, USER_PASSWORD)
    unfiltered = await app_client.get(
        f"/api/orgs/{slug}/search",
        params={"q": "takrenovering"},
    )
    assert unfiltered.status_code == 200, unfiltered.text
    assert unfiltered.json()["result_count"] == 2
    by_id = {r["document_id"]: r for r in unfiltered.json()["results"]}
    assert by_id[str(protokoll_doc)]["category_label"] == "protokoll"

    filtered = await app_client.get(
        f"/api/orgs/{slug}/search",
        params={"q": "takrenovering", "category": str(cluster_ids[0])},
    )
    assert filtered.status_code == 200, filtered.text
    body = filtered.json()
    assert body["result_count"] == 1
    assert body["results"][0]["document_id"] == str(protokoll_doc)
    assert body["results"][0]["category_label"] == "protokoll"


async def test_recluster_accepted(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    _, slug, email = await _seed_member(migrations_pool, "requeue")

    await login(app_client, email, USER_PASSWORD)
    response = await app_client.post(f"/api/orgs/{slug}/clusters/recluster")
    assert response.status_code == 202, response.text
    assert response.json() == {"enqueued": True}


async def test_recluster_conflicts_with_in_flight_run(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id, slug, email = await _seed_member(migrations_pool, "conflict")
    await _seed_run_with_clusters(migrations_pool, org_id, [], status="running")

    await login(app_client, email, USER_PASSWORD)
    response = await app_client.post(f"/api/orgs/{slug}/clusters/recluster")
    assert response.status_code == 409, response.text


async def test_clusters_not_visible_across_orgs(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    org_a, slug_a, _ = await _seed_member(migrations_pool, "tenant-a")
    await _seed_run_with_clusters(migrations_pool, org_a, ["protokoll"])
    _, _, email_b = await _seed_member(migrations_pool, "tenant-b")

    await login(app_client, email_b, USER_PASSWORD)
    response = await app_client.get(f"/api/orgs/{slug_a}/clusters")
    assert response.status_code == 403, response.text


async def test_cluster_documents_lists_members(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id, slug, email = await _seed_member(migrations_pool, "members")
    _, cluster_ids = await _seed_run_with_clusters(migrations_pool, org_id, ["protokoll"])
    doc = await _seed_document_in_cluster(
        migrations_pool,
        org_id,
        "styrelseprotokoll.md",
        "Styrelsen beslutade.",
        cluster_ids[0],
    )
    await _seed_document_in_cluster(
        migrations_pool,
        org_id,
        "utanför-kluster.md",
        "Ingen kategori.",
        None,
    )

    await login(app_client, email, USER_PASSWORD)
    response = await app_client.get(f"/api/orgs/{slug}/clusters/{cluster_ids[0]}/documents")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [d["id"] for d in body] == [str(doc)]
    assert body[0]["title"] == "styrelseprotokoll.md"


async def test_cluster_documents_cross_org_is_not_found(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    org_a, _, _ = await _seed_member(migrations_pool, "member-a")
    _, cluster_ids = await _seed_run_with_clusters(migrations_pool, org_a, ["protokoll"])
    _, slug_b, email_b = await _seed_member(migrations_pool, "member-b")

    await login(app_client, email_b, USER_PASSWORD)
    response = await app_client.get(f"/api/orgs/{slug_b}/clusters/{cluster_ids[0]}/documents")
    assert response.status_code == 404, response.text
