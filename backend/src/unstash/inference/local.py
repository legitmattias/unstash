"""Clients for the self-hosted inference service.

Implement the ``Embedder`` and ``Reranker`` interfaces against
``unstash.inference_server``. The service is unauthenticated and
reachable only on the internal network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from unstash.documents.embedder import EmbeddingBatch, EmbeddingError, EmbeddingTask
from unstash.inference._http import RetryingPoster
from unstash.search.reranker import RerankError, RerankResult

if TYPE_CHECKING:
    import httpx

# Mirrors unstash.inference_server.schemas.MAX_TEXTS; larger inputs are
# sent as several requests.
_MAX_TEXTS_PER_REQUEST = 64


class LocalEmbedder:
    """Implements the ``Embedder`` protocol against the inference service."""

    def __init__(
        self,
        *,
        base_url: str,
        dimensions: int,
        timeout: float,
        max_retries: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure the client; no network happens until :meth:`embed`."""
        self._http = RetryingPoster(
            url=f"{base_url.rstrip('/')}/embed",
            headers={},
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
            error_cls=EmbeddingError,
            label="Inference service",
        )
        self.embedding_dim = dimensions

    async def embed(self, texts: list[str], *, task: EmbeddingTask) -> EmbeddingBatch:
        """Embed ``texts`` via the inference service."""
        vectors: list[list[float]] = []
        total_tokens = 0
        wire_task = "query" if task is EmbeddingTask.QUERY else "passage"
        for start in range(0, len(texts), _MAX_TEXTS_PER_REQUEST):
            part = texts[start : start + _MAX_TEXTS_PER_REQUEST]
            response = await self._http.post({"texts": part, "task": wire_task})
            try:
                body = response.json()
                part_vectors = [[float(value) for value in vector] for vector in body["vectors"]]
                total_tokens += int(body["total_tokens"])
            except (KeyError, TypeError, ValueError) as exc:
                msg = f"Unexpected inference service response shape: {exc}"
                raise EmbeddingError(msg) from exc
            if len(part_vectors) != len(part):
                msg = (
                    f"Inference service returned {len(part_vectors)} vectors for {len(part)} inputs"
                )
                raise EmbeddingError(msg)
            if any(len(vector) != self.embedding_dim for vector in part_vectors):
                msg = (
                    f"Inference service returned vectors of a width other than "
                    f"{self.embedding_dim}; the served model does not match the configuration."
                )
                raise EmbeddingError(msg)
            vectors.extend(part_vectors)
        return EmbeddingBatch(vectors=vectors, total_tokens=total_tokens)


class LocalReranker:
    """Implements the ``Reranker`` protocol against the inference service."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        max_retries: int = 1,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure the client; no network happens until :meth:`rerank`."""
        self._http = RetryingPoster(
            url=f"{base_url.rstrip('/')}/rerank",
            headers={},
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
            error_cls=RerankError,
            label="Inference service rerank",
        )

    async def rerank(self, query: str, documents: list[str]) -> RerankResult:
        """Rerank ``documents`` against ``query`` via the inference service."""
        if not documents:
            return RerankResult(order=[], scores=[])

        response = await self._http.post({"query": query, "documents": documents})
        try:
            scores = [float(score) for score in response.json()["scores"]]
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"Unexpected inference service rerank response shape: {exc}"
            raise RerankError(msg) from exc
        if len(scores) != len(documents):
            msg = f"Inference service returned {len(scores)} scores for {len(documents)} documents"
            raise RerankError(msg)

        order = sorted(range(len(documents)), key=lambda i: -scores[i])
        return RerankResult(order=order, scores=[scores[i] for i in order])
