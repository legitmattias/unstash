"""Rerank client for search-result ordering.

A ``Reranker`` interface with a real Jina client and a deterministic
fake, selected by ``reranker_backend``. Callers treat rerank failure as
non-fatal: the search service degrades to fusion order when this client
raises (rerank improves ordering; it is never required for results).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
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


_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff: 0.5s, 1s, 2s, ..."""
    return 0.5 * (2**attempt)


class JinaReranker:
    """Calls the Jina rerank API with bounded exponential-backoff retries."""

    def __init__(  # noqa: PLR0913 — provider config; grouping into an object buys nothing
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float,
        max_retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure the client; no network happens until :meth:`rerank`.

        ``http_client`` shares a connection pool across requests (the
        caller owns its lifecycle); without it every call pays a fresh
        TCP + TLS handshake.
        """
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/rerank"
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._http_client = http_client

    async def rerank(self, query: str, documents: list[str]) -> RerankResult:
        """Rerank ``documents`` against ``query`` via the Jina API."""
        if not documents:
            return RerankResult(order=[], scores=[])

        payload: dict[str, object] = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
            "return_documents": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        response = await self._post_with_retries(payload, headers)

        try:
            results = response.json()["results"]
            order = [int(row["index"]) for row in results]
            scores = [float(row["relevance_score"]) for row in results]
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"Unexpected Jina rerank response shape: {exc}"
            raise RerankError(msg) from exc

        if sorted(order) != list(range(len(documents))):
            msg = "Jina rerank response does not cover all candidates"
            raise RerankError(msg)
        return RerankResult(order=order, scores=scores)

    async def _post_with_retries(
        self,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> httpx.Response:
        if self._http_client is not None:
            return await self._attempt_loop(self._http_client, payload, headers)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await self._attempt_loop(client, payload, headers)

    async def _attempt_loop(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.post(
                    self._url,
                    json=payload,
                    headers=headers,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.is_success:
                    return response
                if response.status_code not in _RETRYABLE_STATUS:
                    detail = response.text[:300]
                    msg = f"Jina rerank returned {response.status_code}: {detail}"
                    raise RerankError(msg)
                last_error = RerankError(
                    f"Jina rerank returned retryable {response.status_code}",
                )
            if attempt < self._max_retries:
                await asyncio.sleep(_backoff_seconds(attempt))

        msg = f"Jina rerank failed after {self._max_retries + 1} attempts: {last_error}"
        raise RerankError(msg) from last_error


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
    """Return the configured reranker: the fake when selected, else Jina."""
    if settings.reranker_backend == "fake":
        return FakeReranker()
    return JinaReranker(
        api_key=settings.jina_api_key,
        base_url=settings.jina_base_url,
        model=settings.jina_rerank_model,
        timeout=settings.jina_timeout_seconds,
        http_client=http_client,
    )
