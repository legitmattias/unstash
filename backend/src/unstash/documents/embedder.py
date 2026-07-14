"""Embedding client for chunk indexing (M3-C).

Turns chunk text into dense vectors. A small interface with a real Jina
client and a deterministic fake keeps the worker testable offline and
isolates the provider behind one seam; query embedding for search reuses
the same interface at M4.

The backend is config-selected (``embedder_backend``) so the CI smoke and
unit tests can run the full upload-to-indexed flow with the fake, the same
way the Taskiq broker swaps to its in-memory implementation.
"""

from __future__ import annotations

import asyncio
import hashlib
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from unstash.config import Settings


class EmbeddingTask(StrEnum):
    """Jina asymmetric retrieval task: passages are indexed, queries searched."""

    PASSAGE = "retrieval.passage"
    QUERY = "retrieval.query"


class EmbeddingError(RuntimeError):
    """Raised when embeddings cannot be produced."""


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """Vectors for a batch of inputs, plus the provider's token count."""

    vectors: list[list[float]]
    total_tokens: int


class Embedder(Protocol):
    """Produces dense vectors for text. Implemented by Jina and by a fake."""

    embedding_dim: int

    async def embed(self, texts: list[str], *, task: EmbeddingTask) -> EmbeddingBatch:
        """Return vectors for ``texts`` embedded for the given task."""
        ...


# Transient HTTP statuses worth retrying; other non-2xx fail immediately.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff: 0.5s, 1s, 2s, ..."""
    return 0.5 * (2**attempt)


class JinaEmbedder:
    """Calls the Jina embeddings API with bounded exponential-backoff retries."""

    def __init__(  # noqa: PLR0913 — provider config; grouping into an object buys nothing
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int,
        timeout: float,
        max_retries: int = 3,
    ) -> None:
        """Configure the client; no network happens until :meth:`embed`."""
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._model = model
        self._dimensions = dimensions
        self._timeout = timeout
        self._max_retries = max_retries
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
        headers = {"Authorization": f"Bearer {self._api_key}"}
        response = await self._post_with_retries(payload, headers)

        try:
            ordered = sorted(response.json()["data"], key=lambda row: row["index"])
            vectors = [row["embedding"] for row in ordered]
            total_tokens = int(response.json().get("usage", {}).get("total_tokens", 0))
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"Unexpected Jina response shape: {exc}"
            raise EmbeddingError(msg) from exc

        if len(vectors) != len(texts):
            msg = f"Jina returned {len(vectors)} vectors for {len(texts)} inputs"
            raise EmbeddingError(msg)
        return EmbeddingBatch(vectors=vectors, total_tokens=total_tokens)

    async def _post_with_retries(
        self,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> httpx.Response:
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    response = await client.post(self._url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    last_error = exc
                else:
                    if response.is_success:
                        return response
                    if response.status_code not in _RETRYABLE_STATUS:
                        detail = response.text[:300]
                        msg = f"Jina returned {response.status_code}: {detail}"
                        raise EmbeddingError(msg)
                    last_error = EmbeddingError(
                        f"Jina returned retryable {response.status_code}",
                    )
                if attempt < self._max_retries:
                    await asyncio.sleep(_backoff_seconds(attempt))

        msg = f"Jina request failed after {self._max_retries + 1} attempts: {last_error}"
        raise EmbeddingError(msg) from last_error


class FakeEmbedder:
    """Deterministic offline embedder for tests and the CI smoke flow.

    The same text always yields the same L2-normalised vector; different
    texts almost always differ. No network, no model.
    """

    def __init__(self, *, dimensions: int) -> None:
        """Fix the output dimensionality."""
        self.embedding_dim = dimensions

    async def embed(self, texts: list[str], *, task: EmbeddingTask) -> EmbeddingBatch:
        """Return deterministic vectors for ``texts`` (task is ignored)."""
        _ = task
        vectors = [self._vector(text) for text in texts]
        total_tokens = sum(max(1, len(text) // 4) for text in texts)
        return EmbeddingBatch(vectors=vectors, total_tokens=total_tokens)

    def _vector(self, text: str) -> list[float]:
        raw = bytearray()
        block = 0
        while len(raw) < self.embedding_dim * 4:
            raw.extend(hashlib.sha256(f"{block}:{text}".encode()).digest())
            block += 1
        floats = [
            struct.unpack_from("<i", raw, offset=i * 4)[0] / 2**31
            for i in range(self.embedding_dim)
        ]
        norm = sum(value * value for value in floats) ** 0.5 or 1.0
        return [value / norm for value in floats]


def get_embedder(settings: Settings) -> Embedder:
    """Return the configured embedder: the fake when selected, else Jina."""
    if settings.embedder_backend == "fake":
        return FakeEmbedder(dimensions=settings.jina_embedding_dimensions)
    return JinaEmbedder(
        api_key=settings.jina_api_key,
        base_url=settings.jina_base_url,
        model=settings.jina_embedding_model,
        dimensions=settings.jina_embedding_dimensions,
        timeout=settings.jina_timeout_seconds,
    )
