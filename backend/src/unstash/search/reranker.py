"""Rerank interface for search-result ordering.

Defines the ``Reranker`` protocol, its data and error types, a
deterministic fake for offline tests, and the factory that selects the
configured backend. Provider clients live under ``unstash.inference``.

Callers treat rerank failure as non-fatal: the search service degrades
to fusion order when a reranker raises (rerank improves ordering; it is
never required for results).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import httpx

    from unstash.config import Settings


class RerankError(RuntimeError):
    """Raised when reranking cannot be performed."""


@dataclass(frozen=True, slots=True)
class RerankResult:
    """Candidate ordering produced by the reranker.

    ``order`` holds indices into the caller's candidate list, best first.
    ``scores`` aligns with ``order``.
    """

    order: list[int]
    scores: list[float]


class Reranker(Protocol):
    """Orders candidate texts by relevance to a query."""

    async def rerank(self, query: str, documents: list[str]) -> RerankResult:
        """Return the candidate ordering for ``documents`` against ``query``."""
        ...


class FakeReranker:
    """Deterministic offline reranker for tests.

    Scores each candidate by token overlap with the query, tie-broken by
    a stable content hash, so orderings are reproducible without a model.
    """

    async def rerank(self, query: str, documents: list[str]) -> RerankResult:
        """Order ``documents`` by token overlap with ``query``."""
        query_tokens = set(query.lower().split())

        def score(text: str) -> float:
            tokens = set(text.lower().split())
            return len(query_tokens & tokens) / (len(query_tokens) or 1)

        def tiebreak(text: str) -> int:
            return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")

        scored = sorted(
            range(len(documents)),
            key=lambda i: (-score(documents[i]), tiebreak(documents[i])),
        )
        return RerankResult(
            order=scored,
            scores=[score(documents[i]) for i in scored],
        )


def get_reranker(
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> Reranker:
    """Return the configured reranker: the fake when selected, else the provider."""
    if settings.reranker_backend == "fake":
        return FakeReranker()
    # Imported here: the provider module implements this module's
    # interface, so a top-level import would be circular.
    from unstash.inference.jina import JinaReranker  # noqa: PLC0415

    return JinaReranker(
        api_key=settings.jina_api_key,
        base_url=settings.jina_base_url,
        model=settings.jina_rerank_model,
        timeout=settings.jina_timeout_seconds,
        http_client=http_client,
    )
