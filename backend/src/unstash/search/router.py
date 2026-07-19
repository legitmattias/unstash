"""Search endpoints under ``/api/orgs/{slug}/search``.

``GET /search`` runs the hybrid pipeline and writes a ``search_logs``
row inside the same org-scoped transaction as the retrieval queries.
``POST /search/{search_id}/click`` appends a click event to that row —
the ranking feedback signal.
"""

from __future__ import annotations

import time
import uuid  # noqa: TC003
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from unstash.config import get_settings
from unstash.db.models import Document, SearchLog
from unstash.documents.embedder import EmbeddingError, get_embedder
from unstash.orgs.dependencies import CurrentUserDep, OrgContextDep
from unstash.search.reranker import get_reranker
from unstash.search.schemas import (
    ClickReport,
    ClickResponse,
    SearchResponse,
    SearchResultItem,
)
from unstash.search.service import run_search

if TYPE_CHECKING:
    from unstash.config import Settings
    from unstash.search.service import SearchOutcome

search_router = APIRouter()

QueryParam = Annotated[str, Query(min_length=1, max_length=1000, alias="q")]


def _logged_results(outcome: SearchOutcome) -> list[dict[str, Any]]:
    """The shown result set as JSON-serialisable rows with rank and scores."""
    return [
        {
            "document_id": str(hit.document_id),
            "rank": rank,
            "score": hit.score,
            "rerank_score": hit.rerank_score,
        }
        for rank, hit in enumerate(outcome.hits, start=1)
    ]


def _ranking_config(outcome: SearchOutcome, settings: Settings) -> dict[str, Any]:
    """The configuration that produced the results, for click attribution."""
    return {
        "reranked": outcome.reranked,
        "bm25_used": outcome.bm25_used,
        "rrf_k": settings.search_rrf_k,
        "vector_weight": settings.search_vector_weight,
        "candidate_pool": settings.search_candidate_pool,
        "embedder_backend": settings.embedder_backend,
        "embedder_model": settings.jina_embedding_model,
        "reranker_backend": settings.reranker_backend,
        "reranker_model": settings.jina_rerank_model,
    }


@search_router.get("/orgs/{slug}/search", response_model=SearchResponse)
async def search(
    request: Request,
    ctx: OrgContextDep,
    user: CurrentUserDep,
    q: QueryParam,
) -> SearchResponse:
    """Run a hybrid search and log it."""
    settings = get_settings()
    # Absent outside the lifespan (e.g. ASGI test transports); clients
    # then fall back to a per-call connection.
    http_client = getattr(request.app.state, "http_client", None)
    started = time.perf_counter()
    try:
        outcome = await run_search(
            ctx.session,
            org_id=ctx.org_id,
            query=q,
            embedder=get_embedder(settings, http_client=http_client),
            reranker=get_reranker(settings, http_client=http_client),
            settings=settings,
        )
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search is temporarily unavailable.",
        ) from exc
    latency_ms = int((time.perf_counter() - started) * 1000)

    log_row = SearchLog(
        org_id=ctx.org_id,
        user_id=user.id,
        query=q,
        result_count=len(outcome.hits),
        latency_ms=latency_ms,
        top_document_id=outcome.hits[0].document_id if outcome.hits else None,
        results=_logged_results(outcome),
        ranking_config=_ranking_config(outcome, settings),
    )
    ctx.session.add(log_row)
    await ctx.session.flush()

    return SearchResponse(
        search_id=log_row.id,
        query=q,
        results=[
            SearchResultItem(
                document_id=hit.document_id,
                title=hit.title,
                mime_type=hit.mime_type,
                chunk_id=hit.chunk_id,
                excerpt=hit.excerpt,
                score=hit.score,
                rerank_score=hit.rerank_score,
            )
            for hit in outcome.hits
        ],
        result_count=len(outcome.hits),
        latency_ms=latency_ms,
        reranked=outcome.reranked,
        bm25_used=outcome.bm25_used,
    )


@search_router.post(
    "/orgs/{slug}/search/{search_id}/click",
    response_model=ClickResponse,
)
async def report_click(
    ctx: OrgContextDep,
    search_id: uuid.UUID,
    body: ClickReport,
) -> ClickResponse:
    """Append a click event to a search log row.

    Records the clicked document, its position in the shown results (if
    present), and a timestamp. Multiple clicks on one search accumulate;
    ``clicked_document_id`` tracks the latest.
    """
    document_exists = await ctx.session.scalar(
        select(Document.id).where(
            Document.id == body.document_id,
            Document.org_id == ctx.org_id,
        ),
    )
    if document_exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    log_row = await ctx.session.get(SearchLog, search_id)
    if log_row is None or log_row.org_id != ctx.org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Search not found.",
        )

    position = next(
        (row["rank"] for row in log_row.results if row.get("document_id") == str(body.document_id)),
        None,
    )
    # Reassign rather than mutate in place so SQLAlchemy flushes the JSONB.
    log_row.clicks = [
        *log_row.clicks,
        {
            "document_id": str(body.document_id),
            "position": position,
            "clicked_at": datetime.now(UTC).isoformat(),
        },
    ]
    log_row.clicked_document_id = body.document_id
    return ClickResponse(search_id=search_id, clicked_document_id=body.document_id)
