"""Jina AI API clients: embeddings and reranking.

Everything Jina-specific lives here — request payloads, response
shapes, retry semantics. The generic interfaces these implement
(``Embedder``, ``Reranker``) and their error types stay with their
domains; swapping providers means a new module beside this one plus a
factory branch, with no call-site changes.

Both clients accept an optional injected ``httpx.AsyncClient`` so a
process can share one connection pool across requests (the caller owns
its lifecycle); without it every call pays a fresh TCP + TLS handshake.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from unstash.documents.embedder import EmbeddingBatch, EmbeddingError
from unstash.search.reranker import RerankError, RerankResult

if TYPE_CHECKING:
    from unstash.documents.embedder import EmbeddingTask

# Transient HTTP statuses worth retrying; other non-2xx fail immediately.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff: 0.5s, 1s, 2s, ..."""
    return 0.5 * (2**attempt)


class _JinaHttp:
    """Shared POST-with-retries plumbing for the Jina clients."""

    def __init__(  # noqa: PLR0913 — transport config; grouping into an object buys nothing
        self,
        *,
        api_key: str,
        url: str,
        timeout: float,
        max_retries: int,
        http_client: httpx.AsyncClient | None,
        error_cls: type[Exception],
        label: str,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._timeout = timeout
        self._max_retries = max_retries
        self._http_client = http_client
        self._error_cls = error_cls
        self._label = label

    async def post(self, payload: dict[str, object]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
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
                    msg = f"{self._label} returned {response.status_code}: {detail}"
                    raise self._error_cls(msg)
                last_error = self._error_cls(
                    f"{self._label} returned retryable {response.status_code}",
                )
            if attempt < self._max_retries:
                await asyncio.sleep(_backoff_seconds(attempt))

        msg = f"{self._label} request failed after {self._max_retries + 1} attempts: {last_error}"
        raise self._error_cls(msg) from last_error


class JinaEmbedder:
    """Implements the ``Embedder`` protocol against the Jina embeddings API."""

    def __init__(  # noqa: PLR0913 — provider config; grouping into an object buys nothing
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int,
        timeout: float,
        max_retries: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure the client; no network happens until :meth:`embed`."""
        self._model = model
        self._dimensions = dimensions
        self._http = _JinaHttp(
            api_key=api_key,
            url=f"{base_url.rstrip('/')}/embeddings",
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
            error_cls=EmbeddingError,
            label="Jina",
        )
        self.embedding_dim = dimensions

    async def embed(self, texts: list[str], *, task: EmbeddingTask) -> EmbeddingBatch:
        """Embed ``texts`` via the Jina API, retrying transient failures."""
        if not texts:
            return EmbeddingBatch(vectors=[], total_tokens=0)

        payload: dict[str, object] = {
            "model": self._model,
            "task": task.value,
            "dimensions": self._dimensions,
            "input": [{"text": text} for text in texts],
        }
        response = await self._http.post(payload)

        try:
            body = response.json()
            ordered = sorted(body["data"], key=lambda row: row["index"])
            vectors = [row["embedding"] for row in ordered]
            total_tokens = int(body.get("usage", {}).get("total_tokens", 0))
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"Unexpected Jina response shape: {exc}"
            raise EmbeddingError(msg) from exc

        if len(vectors) != len(texts):
            msg = f"Jina returned {len(vectors)} vectors for {len(texts)} inputs"
            raise EmbeddingError(msg)
        return EmbeddingBatch(vectors=vectors, total_tokens=total_tokens)


class JinaReranker:
    """Implements the ``Reranker`` protocol against the Jina rerank API."""

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
        """Configure the client; no network happens until :meth:`rerank`."""
        self._model = model
        self._http = _JinaHttp(
            api_key=api_key,
            url=f"{base_url.rstrip('/')}/rerank",
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
            error_cls=RerankError,
            label="Jina rerank",
        )

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
        response = await self._http.post(payload)

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
