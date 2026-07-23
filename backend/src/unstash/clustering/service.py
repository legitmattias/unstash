"""Clustering service — loads documents, runs the engine, persists results.

The engine (:mod:`unstash.clustering.engine`) is pure; this module owns the
database sides: pooling chunk embeddings to document level, recording
:class:`ClusteringRun` rows, writing :class:`Cluster` rows with
keyword-fallback labels, re-pointing ``documents.cluster_id``, and the
re-cluster trigger decision.

A run spans three transactions — record the run, compute (no transaction
open), persist the outcome — so a crash mid-compute leaves a ``running``
row that a later run supersedes rather than a long-lived open transaction.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import structlog
from sqlalchemy import func, select, update

from unstash.clustering.engine import (
    MIN_DOCUMENTS,
    cluster_documents,
    keyword_label,
)
from unstash.clustering.labeler import LabelError, LabelRequest, LabelResult, get_labeler
from unstash.db.models import (
    Chunk,
    Cluster,
    ClusteringRun,
    ClusteringRunStatus,
    ClusteringTrigger,
    ClusterLabelSource,
    Document,
    DocumentStatus,
    LlmCallOutcome,
)
from unstash.llm import record_llm_call
from unstash.tasks.context import org_context

if TYPE_CHECKING:
    import uuid

    import numpy.typing as npt
    from sqlalchemy.ext.asyncio import AsyncSession

    from unstash.clustering.engine import ClusteringOutput

logger = structlog.get_logger(__name__)

# Leading document content passed to c-TF-IDF alongside the title. Keyword
# extraction needs enough text to find discriminative terms, not the full
# document.
_LEAD_TEXT_CHARS = 2000
# Excerpt length per representative document in the labeling prompt —
# data-minimal by design (ADR 0011): keywords + titles + a sentence or two.
_LABEL_EXCERPT_CHARS = 200
_DOUBLING_FACTOR = 2


@dataclass(frozen=True)
class OrgCorpus:
    """Indexed documents of one org, pooled to document level."""

    document_ids: list[uuid.UUID]
    texts: list[str]
    embeddings: npt.NDArray[np.float64]


def evaluate_trigger(
    indexed_count: int,
    last_run_count: int | None,
    min_documents: int,
) -> ClusteringTrigger | None:
    """Decide whether a clustering run is due.

    Args:
        indexed_count: The org's current number of indexed documents.
        last_run_count: ``document_count`` of the latest succeeded run,
            or ``None`` when the org has never been clustered.
        min_documents: Org size at which clustering starts.

    Returns:
        The trigger to record on the run, or ``None`` when no run is due.
    """
    if indexed_count < min_documents:
        return None
    if last_run_count is None:
        return ClusteringTrigger.THRESHOLD
    if indexed_count >= _DOUBLING_FACTOR * last_run_count:
        return ClusteringTrigger.DOUBLING
    return None


async def pending_trigger(session: AsyncSession, org_id: uuid.UUID) -> ClusteringTrigger | None:
    """Evaluate the re-cluster trigger against the database.

    Returns ``None`` while a run is already in flight, so concurrent
    ingestion completions do not stack duplicate runs.
    """
    from unstash.config import get_settings  # noqa: PLC0415

    in_flight = await session.scalar(
        select(func.count())
        .select_from(ClusteringRun)
        .where(
            ClusteringRun.org_id == org_id,
            ClusteringRun.status == ClusteringRunStatus.RUNNING,
        ),
    )
    if in_flight:
        return None

    indexed_count = await session.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.org_id == org_id,
            Document.status == DocumentStatus.INDEXED,
        ),
    )
    last_run_count = await session.scalar(
        select(ClusteringRun.document_count)
        .where(
            ClusteringRun.org_id == org_id,
            ClusteringRun.status == ClusteringRunStatus.SUCCEEDED,
        )
        .order_by(ClusteringRun.created_at.desc())
        .limit(1),
    )
    return evaluate_trigger(
        indexed_count or 0,
        last_run_count,
        get_settings().clustering_min_documents,
    )


async def load_org_corpus(session: AsyncSession, org_id: uuid.UUID) -> OrgCorpus:
    """Load the org's indexed documents with pooled embeddings.

    Documents whose chunks are all unembedded are excluded — a document
    that failed mid-embed can retain committed chunks, so pooling only
    considers non-NULL embeddings. Order is (created_at, id) so identical
    corpus states produce identical engine input.
    """
    rows = (
        (
            await session.execute(
                select(Document.id, Document.title)
                .where(
                    Document.org_id == org_id,
                    Document.status == DocumentStatus.INDEXED,
                )
                .order_by(Document.created_at, Document.id),
            )
        )
        .tuples()
        .all()
    )
    titles = dict(rows)

    chunk_rows = (
        await session.execute(
            select(Chunk.document_id, Chunk.embedding).where(
                Chunk.org_id == org_id,
                Chunk.embedding.is_not(None),
            ),
        )
    ).all()
    by_document: dict[uuid.UUID, list[list[float]]] = {}
    for doc_id, embedding in chunk_rows:
        by_document.setdefault(doc_id, []).append(embedding)

    lead_rows = (
        (
            await session.execute(
                select(Chunk.document_id, Chunk.text).where(
                    Chunk.org_id == org_id,
                    Chunk.chunk_index == 0,
                ),
            )
        )
        .tuples()
        .all()
    )
    lead_texts = dict(lead_rows)

    document_ids: list[uuid.UUID] = []
    texts: list[str] = []
    pooled: list[npt.NDArray[np.float64]] = []
    for doc_id, _title in rows:
        vectors = by_document.get(doc_id)
        if not vectors:
            continue
        document_ids.append(doc_id)
        lead = lead_texts.get(doc_id, "")[:_LEAD_TEXT_CHARS]
        texts.append(f"{titles[doc_id]}\n{lead}")
        pooled.append(np.asarray(vectors, dtype=np.float64).mean(axis=0))

    embeddings = np.stack(pooled) if pooled else np.empty((0, 0), dtype=np.float64)
    return OrgCorpus(document_ids=document_ids, texts=texts, embeddings=embeddings)


async def execute_clustering(
    org_id: uuid.UUID,
    triggered_by: ClusteringTrigger,
) -> uuid.UUID | None:
    """Run one clustering pass for an org and persist the outcome.

    Returns the run id, or ``None`` when the org is below the engine
    minimum (possible on a manual trigger) or the run failed.
    """
    async with org_context(org_id) as session:
        corpus = await load_org_corpus(session, org_id)
        if len(corpus.document_ids) < MIN_DOCUMENTS:
            logger.info(
                "clustering_skipped",
                org_id=str(org_id),
                document_count=len(corpus.document_ids),
                minimum=MIN_DOCUMENTS,
            )
            return None
        run = ClusteringRun(
            org_id=org_id,
            triggered_by=triggered_by,
            document_count=len(corpus.document_ids),
            params={},
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    try:
        output = await asyncio.to_thread(
            cluster_documents,
            corpus.texts,
            corpus.embeddings,
        )
    except Exception:
        logger.exception("clustering_failed", org_id=str(org_id), run_id=str(run_id))
        async with org_context(org_id) as session:
            await session.execute(
                update(ClusteringRun)
                .where(ClusteringRun.id == run_id)
                .values(status=ClusteringRunStatus.FAILED, error="engine failure"),
            )
        return None

    async with org_context(org_id) as session:
        cluster_ids = await _persist_outcome(session, org_id, run_id, corpus, output)

    # Labeling is best-effort on top of a run that already succeeded with
    # keyword labels; a crash here must not fail the run.
    try:
        await _apply_llm_labels(org_id, corpus, output, cluster_ids)
    except Exception:
        logger.exception("cluster_labeling_failed", org_id=str(org_id), run_id=str(run_id))

    logger.info(
        "clustering_completed",
        org_id=str(org_id),
        run_id=str(run_id),
        document_count=len(corpus.document_ids),
        cluster_count=len(output.keywords),
        noise_count=sum(1 for a in output.assignments if a == -1),
        silhouette=output.silhouette,
    )
    return run_id


async def _persist_outcome(
    session: AsyncSession,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    corpus: OrgCorpus,
    output: ClusteringOutput,
) -> dict[int, uuid.UUID]:
    """Write clusters, re-point document assignments, close the run.

    Returns the engine-cluster-id → cluster-row-id mapping for labeling.
    """
    clusters: dict[int, Cluster] = {}
    for engine_id, terms in output.keywords.items():
        member_count = sum(1 for a in output.assignments if a == engine_id)
        cluster = Cluster(
            org_id=org_id,
            run_id=run_id,
            label=keyword_label(terms),
            keywords=[{"term": term, "score": score} for term, score in terms],
            representative_document_ids=[
                str(corpus.document_ids[i]) for i in output.representatives[engine_id]
            ],
            size=member_count,
        )
        session.add(cluster)
        clusters[engine_id] = cluster
    await session.flush()

    # Assignments from prior runs are cleared first; a document that this
    # run marked as noise (or that is no longer indexed) must not keep a
    # stale cluster.
    await session.execute(
        update(Document).where(Document.org_id == org_id).values(cluster_id=None),
    )
    for engine_id, cluster in clusters.items():
        member_ids = [
            corpus.document_ids[i]
            for i, assigned in enumerate(output.assignments)
            if assigned == engine_id
        ]
        await session.execute(
            update(Document)
            .where(Document.org_id == org_id, Document.id.in_(member_ids))
            .values(cluster_id=cluster.id),
        )

    await session.execute(
        update(ClusteringRun)
        .where(ClusteringRun.id == run_id)
        .values(
            status=ClusteringRunStatus.SUCCEEDED,
            cluster_count=len(output.keywords),
            noise_count=sum(1 for a in output.assignments if a == -1),
            silhouette=output.silhouette,
            params=output.params,
        ),
    )
    return {engine_id: cluster.id for engine_id, cluster in clusters.items()}


async def _apply_llm_labels(
    org_id: uuid.UUID,
    corpus: OrgCorpus,
    output: ClusteringOutput,
    cluster_ids: dict[int, uuid.UUID],
) -> None:
    """Upgrade keyword labels to LLM labels, one call per cluster.

    Calls run outside any transaction; results and audit rows are written
    in one org-scoped transaction afterwards. A failed call keeps that
    cluster's keyword label and still gets an audit row.
    """
    from unstash.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    labeler = get_labeler(settings)
    if labeler is None or not cluster_ids:
        return

    capped = sorted(cluster_ids)[: settings.labeler_max_calls_per_run]
    if len(capped) < len(cluster_ids):
        logger.warning(
            "cluster_labeling_capped",
            org_id=str(org_id),
            clusters=len(cluster_ids),
            cap=settings.labeler_max_calls_per_run,
        )

    results: list[tuple[int, LabelResult | None, str | None, int]] = []
    for engine_id in capped:
        titles: list[str] = []
        excerpts: list[str] = []
        for index in output.representatives[engine_id]:
            title, _, lead = corpus.texts[index].partition("\n")
            titles.append(title)
            excerpts.append(lead[:_LABEL_EXCERPT_CHARS])
        request = LabelRequest(
            keywords=[term for term, _ in output.keywords[engine_id]],
            representative_titles=titles,
            representative_excerpts=excerpts,
            language=settings.labeler_language,
        )
        call_start = time.perf_counter()
        try:
            result = await labeler.label(request)
            results.append((engine_id, result, None, _elapsed_ms(call_start)))
        except LabelError as exc:
            results.append((engine_id, None, str(exc), _elapsed_ms(call_start)))

    async with org_context(org_id) as session:
        for engine_id, result, error, latency_ms in results:
            if result is not None:
                await session.execute(
                    update(Cluster)
                    .where(Cluster.id == cluster_ids[engine_id])
                    .values(label=result.label, label_source=ClusterLabelSource.LLM),
                )
            await record_llm_call(
                session,
                org_id=org_id,
                purpose="cluster_label",
                model=result.model if result else settings.labeler_model,
                latency_ms=latency_ms,
                outcome=LlmCallOutcome.OK if result else LlmCallOutcome.ERROR,
                prompt_tokens=result.prompt_tokens if result else None,
                completion_tokens=result.completion_tokens if result else None,
                cost=result.cost if result else None,
                error=error,
            )

    labeled = sum(1 for _, result, _, _ in results if result is not None)
    logger.info(
        "cluster_labeling_completed",
        org_id=str(org_id),
        labeled=labeled,
        failed=len(results) - labeled,
    )


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
