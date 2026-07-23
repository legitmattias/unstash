"""Clustering service integration tests against real Postgres.

Seeds an org with indexed documents whose chunk embeddings form planted
Gaussian blobs, runs the full service path (load → engine → persist), and
verifies the database outcome: run row, cluster rows with keyword-fallback
labels, and document assignments. The trigger evaluation is exercised
against the same seeded state.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import numpy as np
import pytest
from sqlalchemy import select

from tests.integration.conftest import (
    TEST_APP_PASSWORD,
    TEST_MIGRATIONS_PASSWORD,
)
from unstash.clustering.labeler import LabelError
from unstash.clustering.service import execute_clustering, load_org_corpus, pending_trigger
from unstash.config import get_settings
from unstash.db.engine import dispose_engine, get_engine
from unstash.db.models import (
    Cluster,
    ClusteringRun,
    ClusteringRunStatus,
    ClusteringTrigger,
    Document,
    LlmCall,
)
from unstash.db.session import get_sessionmaker
from unstash.tasks import org_context

if TYPE_CHECKING:
    import asyncpg

_DIM = 2048
_DOCS_PER_BLOB = 15

_BLOB_TITLES = (
    ("protokoll styrelsemöte", "styrelse beslut protokoll"),
    ("faktura betalning", "faktura belopp förfallodatum"),
    ("avtal leverantör", "avtal uppsägning villkor"),
)


@pytest.fixture
def _engine_env(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> None:
    """Point the app engine at the testcontainer database."""
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
    monkeypatch.setenv("UNSTASH_CLUSTERING_MIN_DOCUMENTS", "40")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _vector_literal(vector: np.ndarray) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


async def _seed_org_with_blobs(pool: asyncpg.Pool, seed: int = 11) -> uuid.UUID:
    """Seed an org with indexed documents forming three embedding blobs."""
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(len(_BLOB_TITLES), _DIM)) * 10.0
    suffix = uuid.uuid4().hex[:8]

    async with pool.acquire() as conn:
        org_id = await conn.fetchval(
            "INSERT INTO organisations (name, slug) VALUES ($1, $2) RETURNING id",
            f"Cluster Org {suffix}",
            f"cluster-{suffix}",
        )
        for blob, (center, (title, lead)) in enumerate(zip(centers, _BLOB_TITLES, strict=True)):
            for i in range(_DOCS_PER_BLOB):
                doc_id = await conn.fetchval(
                    "INSERT INTO documents (org_id, title, source_uri, mime_type, "
                    "size_bytes, content_hash, status, indexed_at) "
                    "VALUES ($1, $2, $3, 'text/plain', 100, $4, 'indexed', now()) "
                    "RETURNING id",
                    org_id,
                    f"{title} {blob}-{i}",
                    f"upload://{suffix}/{blob}/{i}",
                    f"hash-{suffix}-{blob}-{i}",
                )
                embedding = center + rng.normal(size=_DIM)
                await conn.execute(
                    "INSERT INTO chunks (org_id, document_id, chunk_index, text, "
                    "token_count, char_offset_start, char_offset_end, embedding) "
                    "VALUES ($1, $2, 0, $3, 10, 0, 100, $4::vector)",
                    org_id,
                    doc_id,
                    f"{lead} dokument {blob}-{i}",
                    _vector_literal(embedding),
                )
    return org_id


@pytest.mark.usefixtures("_engine_env")
async def test_execute_clustering_persists_full_outcome(
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id = await _seed_org_with_blobs(migrations_pool)
    try:
        run_id = await execute_clustering(org_id, ClusteringTrigger.MANUAL)
        assert run_id is not None

        async with org_context(org_id) as session:
            run = (
                await session.execute(
                    select(ClusteringRun).where(ClusteringRun.id == run_id),
                )
            ).scalar_one()
            assert run.status == ClusteringRunStatus.SUCCEEDED
            assert run.document_count == len(_BLOB_TITLES) * _DOCS_PER_BLOB
            assert run.cluster_count == len(_BLOB_TITLES)
            assert run.params["n_documents"] == run.document_count
            assert run.params["silhouette_space"] == "umap"
            assert run.silhouette is not None

            clusters = (
                (await session.execute(select(Cluster).where(Cluster.run_id == run_id)))
                .scalars()
                .all()
            )
            assert len(clusters) == len(_BLOB_TITLES)
            for cluster in clusters:
                assert cluster.label
                assert cluster.label_source == "keyword_fallback"
                assert cluster.keywords
                assert 1 <= len(cluster.representative_document_ids) <= 3
                assert cluster.size > 0

            documents = (
                (await session.execute(select(Document).where(Document.org_id == org_id)))
                .scalars()
                .all()
            )
            cluster_ids = {c.id for c in clusters}
            assigned = [d for d in documents if d.cluster_id is not None]
            assert all(d.cluster_id in cluster_ids for d in assigned)
            # Well-separated blobs: at most a few noise documents.
            assert len(assigned) >= run.document_count - run.noise_count
    finally:
        await dispose_engine()


@pytest.mark.usefixtures("_engine_env")
async def test_rerun_repoints_documents_to_new_clusters(
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id = await _seed_org_with_blobs(migrations_pool)
    try:
        first_run = await execute_clustering(org_id, ClusteringTrigger.MANUAL)
        second_run = await execute_clustering(org_id, ClusteringTrigger.MANUAL)
        assert first_run is not None
        assert second_run is not None

        async with org_context(org_id) as session:
            new_cluster_ids = {
                c.id
                for c in (
                    await session.execute(select(Cluster).where(Cluster.run_id == second_run))
                )
                .scalars()
                .all()
            }
            documents = (
                (await session.execute(select(Document).where(Document.org_id == org_id)))
                .scalars()
                .all()
            )
            for doc in documents:
                assert doc.cluster_id is None or doc.cluster_id in new_cluster_ids
    finally:
        await dispose_engine()


@pytest.mark.usefixtures("_engine_env")
async def test_pending_trigger_fires_and_deduplicates(
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id = await _seed_org_with_blobs(migrations_pool)
    try:
        async with org_context(org_id) as session:
            # 45 indexed documents, threshold 40, no prior run.
            assert await pending_trigger(session, org_id) == ClusteringTrigger.THRESHOLD

            session.add_all(
                [
                    ClusteringRun(
                        org_id=org_id,
                        triggered_by=ClusteringTrigger.THRESHOLD,
                        document_count=45,
                        params={},
                    ),
                ],
            )
            await session.flush()
            # An in-flight (running) run suppresses further triggers.
            assert await pending_trigger(session, org_id) is None
    finally:
        await dispose_engine()


@pytest.mark.usefixtures("_engine_env")
async def test_corpus_excludes_unembedded_documents(
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id = await _seed_org_with_blobs(migrations_pool)
    async with migrations_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO documents (org_id, title, source_uri, mime_type, "
            "size_bytes, content_hash, status) "
            "VALUES ($1, 'pending doc', 'upload://x', 'text/plain', 1, 'h-x', 'pending')",
            org_id,
        )
    try:
        async with org_context(org_id) as session:
            corpus = await load_org_corpus(session, org_id)
        assert len(corpus.document_ids) == len(_BLOB_TITLES) * _DOCS_PER_BLOB
        assert corpus.embeddings.shape == (len(corpus.document_ids), _DIM)
    finally:
        await dispose_engine()


@pytest.mark.usefixtures("_engine_env")
async def test_fake_labeler_upgrades_labels_and_records_calls(
    migrations_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNSTASH_LABELER_BACKEND", "fake")
    get_settings.cache_clear()
    org_id = await _seed_org_with_blobs(migrations_pool)
    try:
        run_id = await execute_clustering(org_id, ClusteringTrigger.MANUAL)
        assert run_id is not None

        async with org_context(org_id) as session:
            clusters = (
                (await session.execute(select(Cluster).where(Cluster.run_id == run_id)))
                .scalars()
                .all()
            )
            assert clusters
            for cluster in clusters:
                assert cluster.label_source == "llm"
                assert cluster.label[0].isupper()

            calls = (
                (await session.execute(select(LlmCall).where(LlmCall.org_id == org_id)))
                .scalars()
                .all()
            )
            assert len(calls) == len(clusters)
            assert all(c.purpose == "cluster_label" for c in calls)
            assert all(c.outcome == "ok" for c in calls)
    finally:
        await dispose_engine()


@pytest.mark.usefixtures("_engine_env")
async def test_failed_labeling_keeps_keyword_label(
    migrations_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingLabeler:
        async def label(self, request):
            raise LabelError("synthetic failure")

    monkeypatch.setattr(
        "unstash.clustering.service.get_labeler",
        lambda settings: _FailingLabeler(),
    )
    org_id = await _seed_org_with_blobs(migrations_pool)
    try:
        run_id = await execute_clustering(org_id, ClusteringTrigger.MANUAL)
        assert run_id is not None

        async with org_context(org_id) as session:
            clusters = (
                (await session.execute(select(Cluster).where(Cluster.run_id == run_id)))
                .scalars()
                .all()
            )
            assert clusters
            assert all(c.label_source == "keyword_fallback" for c in clusters)

            calls = (
                (await session.execute(select(LlmCall).where(LlmCall.org_id == org_id)))
                .scalars()
                .all()
            )
            assert len(calls) == len(clusters)
            assert all(c.outcome == "error" for c in calls)
            assert all(c.error == "synthetic failure" for c in calls)
    finally:
        await dispose_engine()
