"""Response and request bodies for the search endpoints."""

from __future__ import annotations

import uuid  # noqa: TC003

from pydantic import BaseModel


class SearchResultItem(BaseModel):
    """One ranked document with its best-matching excerpt."""

    document_id: uuid.UUID
    title: str
    mime_type: str
    category_label: str | None
    chunk_id: uuid.UUID
    excerpt: str  # the full best-matching chunk
    snippet: str  # a bounded, match-centred excerpt for the result list
    score: float
    rerank_score: float | None


class SearchResponse(BaseModel):
    """Ranked results plus pipeline provenance for the request."""

    search_id: uuid.UUID
    query: str
    results: list[SearchResultItem]
    result_count: int
    latency_ms: int
    reranked: bool
    bm25_used: bool


class ClickReport(BaseModel):
    """Click attribution for a previously returned search."""

    document_id: uuid.UUID


class ClickResponse(BaseModel):
    """Acknowledgement of a recorded click."""

    search_id: uuid.UUID
    clicked_document_id: uuid.UUID
