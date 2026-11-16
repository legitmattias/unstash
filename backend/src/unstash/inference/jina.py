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

from typing import TYPE_CHECKING

from unstash.documents.embedder import EmbeddingBatch, EmbeddingError
from unstash.inference._http import RetryingPoster
from unstash.search.reranker import RerankError, RerankResult

if TYPE_CHECKING:
    import httpx

    from unstash.documents.embedder import EmbeddingTask


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
        self._http = RetryingPoster(
            headers={"Authorization": f"Bearer {api_key}"},
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
        self._http = RetryingPoster(
            headers={"Authorization": f"Bearer {api_key}"},
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
