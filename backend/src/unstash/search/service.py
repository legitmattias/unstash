"""Hybrid retrieval: vector + BM25, weighted RRF fusion, optional rerank.

The pipeline mirrors the configuration measured on the golden retrieval
set: chunk-level vector and BM25 candidates, weighted Reciprocal Rank
Fusion, best-chunk-per-document reduction, then reranking of document
excerpts. Rerank failure is non-fatal — results fall back to fusion
order (the reranker improves ordering; it never gates availability).

BM25 failure is likewise non-fatal: the query parser can reject exotic
user input, in which case that leg is dropped and fusion degenerates to
vector order for the request.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from unstash.documents.embedder import EmbeddingTask
from unstash.search.reranker import RerankError

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy import RowMapping
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

    from unstash.config import Settings
    from unstash.documents.embedder import Embedder
    from unstash.search.reranker import Reranker

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class SearchHit:
    """One ranked document with its best-matching chunk."""

    document_id: uuid.UUID
    title: str
    mime_type: str
    chunk_id: uuid.UUID
    excerpt: str
    score: float
    rerank_score: float | None


@dataclass(slots=True)
class SearchOutcome:
    """Ranked hits plus how the ranking was produced."""

    hits: list[SearchHit]
    reranked: bool
    bm25_used: bool


@dataclass(slots=True)
class _Candidate:
    document_id: uuid.UUID
    title: str
    mime_type: str
    chunk_id: uuid.UUID
    excerpt: str
    fused_score: float


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Optional metadata filters restricting which documents are searchable."""

    mime_type: str | None = None
    date_from: str | None = None  # inclusive ISO date bound
    date_to: str | None = None  # inclusive ISO date bound

    @property
    def any(self) -> bool:
        """True when at least one filter is set."""
        return any((self.mime_type, self.date_from, self.date_to))


# The date filter matches a document that carries at least one extracted
# date within the range. document_metadata is org-scoped (RLS), and a
# partial ``YYYY-MM`` iso is treated as the first of the month so it can be
# compared as a date.
_DATE_FILTER = (
    " AND EXISTS ("
    "  SELECT 1 FROM document_metadata dm,"
    "  jsonb_array_elements(dm.dates) AS elem"
    "  WHERE dm.document_id = d.id"
    "  AND (CASE WHEN length(elem->>'iso') = 10 THEN (elem->>'iso')::date"
    "            ELSE ((elem->>'iso') || '-01')::date END)"
    "  BETWEEN to_date(:date_from, 'YYYY-MM-DD') AND to_date(:date_to, 'YYYY-MM-DD'))"
)


def _filter_clause(filters: SearchFilters) -> tuple[str, dict[str, object]]:
    """Build an additional WHERE fragment and its bind params.

    Fragment strings are fixed; every user value travels as a bind
    parameter, so the spliced SQL carries no user input.
    """
    fragments: list[str] = []
    params: dict[str, object] = {}
    if filters.mime_type is not None:
        fragments.append(" AND d.mime_type = :mime_type")
        params["mime_type"] = filters.mime_type
    if filters.date_from is not None or filters.date_to is not None:
        fragments.append(_DATE_FILTER)
        params["date_from"] = filters.date_from or "0001-01-01"
        params["date_to"] = filters.date_to or "9999-12-31"
    return "".join(fragments), params


# A document that failed mid-embed can retain committed chunks with
# non-NULL embeddings (the embed task commits partial progress for
# idempotent retry), so searchability is gated on document status, not
# on embedding presence alone.
def _vector_sql(filter_clause: str) -> TextClause:
    # filter_clause is composed only of fixed fragments in _filter_clause;
    # all user values travel as bind parameters, so no user input is spliced.
    return text(
        "SELECT c.id AS chunk_id, c.text, c.document_id, d.title, d.mime_type"  # noqa: S608
        " FROM chunks c JOIN documents d ON d.id = c.document_id"
        " WHERE c.org_id = :org_id AND d.status = 'indexed' AND c.embedding IS NOT NULL"
        f"{filter_clause}"
        " ORDER BY c.embedding <=> CAST(:query_vec AS vector) LIMIT :pool",
    )


def _bm25_sql(filter_clause: str) -> TextClause:
    return text(
        "SELECT c.id AS chunk_id, c.text, c.document_id, d.title, d.mime_type,"  # noqa: S608
        " paradedb.score(c.id) AS score"
        " FROM chunks c JOIN documents d ON d.id = c.document_id"
        " WHERE c.org_id = :org_id AND d.status = 'indexed' AND c.text @@@ :query"
        f"{filter_clause}"
        " ORDER BY score DESC LIMIT :pool",
    )


def fuse_rrf(
    rankings: list[tuple[list[uuid.UUID], float]],
    *,
    k: int,
) -> dict[uuid.UUID, float]:
    """Weighted Reciprocal Rank Fusion over chunk-id rankings."""
    scores: dict[uuid.UUID, float] = defaultdict(float)
    for ranked, weight in rankings:
        for position, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] += weight / (k + position)
    return scores


async def run_search(  # noqa: PLR0913 — pipeline inputs; grouping into an object buys nothing
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    query: str,
    embedder: Embedder,
    reranker: Reranker,
    settings: Settings,
    filters: SearchFilters | None = None,
) -> SearchOutcome:
    """Run the hybrid pipeline for ``query`` inside the org-scoped session."""
    filters = filters or SearchFilters()
    batch = await embedder.embed([query], task=EmbeddingTask.QUERY)
    query_vec = "[" + ",".join(str(v) for v in batch.vectors[0]) + "]"

    # Bound retrieval so a pathological BM25 query (wildcards, large fuzzy
    # expansions) cannot pressure the database. Transaction-local; a BM25
    # timeout is caught by its savepoint below and degrades to vector-only.
    await session.execute(
        text("SELECT set_config('statement_timeout', :timeout_ms, true)").bindparams(
            timeout_ms=str(settings.search_statement_timeout_ms),
        ),
    )

    filter_clause, filter_params = _filter_clause(filters)
    pool = settings.search_candidate_pool
    vector_rows: Sequence[RowMapping] = (
        (
            await session.execute(
                _vector_sql(filter_clause),
                {"org_id": org_id, "query_vec": query_vec, "pool": pool, **filter_params},
            )
        )
        .mappings()
        .all()
    )

    # The savepoint keeps a BM25 parse failure from aborting the enclosing
    # transaction, which still has the search_logs insert ahead of it.
    bm25_used = True
    bm25_rows: Sequence[RowMapping]
    try:
        async with session.begin_nested():
            with_bm25 = await session.execute(
                _bm25_sql(filter_clause),
                {"org_id": org_id, "query": query, "pool": pool, **filter_params},
            )
            bm25_rows = with_bm25.mappings().all()
    except DBAPIError:
        logger.warning("bm25_leg_failed", query_length=len(query))
        bm25_used = False
        bm25_rows = []

    chunk_info: dict[uuid.UUID, RowMapping] = {
        row["chunk_id"]: row for rows in (vector_rows, bm25_rows) for row in rows
    }
    fused = fuse_rrf(
        [
            ([r["chunk_id"] for r in vector_rows], settings.search_vector_weight),
            ([r["chunk_id"] for r in bm25_rows], 1.0),
        ],
        k=settings.search_rrf_k,
    )

    # Reduce to best chunk per document, in fused order.
    candidates: list[_Candidate] = []
    seen_documents: set[uuid.UUID] = set()
    for chunk_id, score in sorted(fused.items(), key=lambda kv: -kv[1]):
        row = chunk_info[chunk_id]
        if row["document_id"] in seen_documents:
            continue
        seen_documents.add(row["document_id"])
        candidates.append(
            _Candidate(
                document_id=row["document_id"],
                title=row["title"],
                mime_type=row["mime_type"],
                chunk_id=chunk_id,
                excerpt=row["text"],
                fused_score=score,
            ),
        )

    limit = settings.search_result_limit
    to_rerank = candidates[: settings.search_rerank_candidates]
    rerank_scores: list[float | None] = [None] * len(to_rerank)
    reranked = False
    if to_rerank:
        try:
            result = await reranker.rerank(query, [c.excerpt for c in to_rerank])
        except RerankError:
            logger.warning("rerank_failed_falling_back_to_fusion")
        else:
            to_rerank = [to_rerank[i] for i in result.order]
            rerank_scores = list(result.scores)
            reranked = True

    hits = [
        SearchHit(
            document_id=c.document_id,
            title=c.title,
            mime_type=c.mime_type,
            chunk_id=c.chunk_id,
            excerpt=c.excerpt,
            score=c.fused_score,
            rerank_score=rerank_scores[i],
        )
        for i, c in enumerate(to_rerank[:limit])
    ]
    return SearchOutcome(hits=hits, reranked=reranked, bm25_used=bm25_used)
