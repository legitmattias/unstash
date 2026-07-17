"""Search endpoints under ``/api/orgs/{slug}/search``.

``GET /search`` runs the hybrid pipeline and writes a ``search_logs``
row inside the same org-scoped transaction as the retrieval queries.
``POST /search/{search_id}/click`` patches click attribution onto an
existing log row — the ranking feedback signal.
"""

from __future__ import annotations

import time
import uuid  # noqa: TC003
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, update

from unstash.config import get_settings
from unstash.db.models import Document, SearchLog
from unstash.documents.embedder import EmbeddingError, get_embedder
from unstash.orgs.dependencies import CurrentUserDep, OrgContext, get_org_context
from unstash.search.reranker import get_reranker
from unstash.search.schemas import (
    ClickReport,
    ClickResponse,
    SearchResponse,
    SearchResultItem,
)
from unstash.search.service import run_search

search_router = APIRouter()

OrgContextDep = Annotated[OrgContext, Depends(get_org_context)]
QueryParam = Annotated[str, Query(min_length=1, max_length=1000, alias="q")]


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
    """Record which document the user opened from a search result."""
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

    updated = await ctx.session.scalar(
        update(SearchLog)
        .where(SearchLog.id == search_id, SearchLog.org_id == ctx.org_id)
        .values(clicked_document_id=body.document_id)
        .returning(SearchLog.id),
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Search not found.",
        )
    return ClickResponse(search_id=search_id, clicked_document_id=body.document_id)
