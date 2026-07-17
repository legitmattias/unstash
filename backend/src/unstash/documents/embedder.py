"""Embedding interface for chunk indexing and query embedding.

Defines the ``Embedder`` protocol, its data and error types, a
deterministic fake for offline tests, and the factory that selects the
configured backend. Provider clients live under ``unstash.inference``.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import httpx

    from unstash.config import Settings


class EmbeddingTask(StrEnum):
    """Asymmetric retrieval task: passages are indexed, queries searched."""

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
    """Produces dense vectors for text. Implemented per provider and by a fake."""

    embedding_dim: int

    async def embed(self, texts: list[str], *, task: EmbeddingTask) -> EmbeddingBatch:
        """Return vectors for ``texts`` embedded for the given task."""
        ...


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


def get_embedder(
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> Embedder:
    """Return the configured embedder: the fake when selected, else the provider."""
    if settings.embedder_backend == "fake":
        return FakeEmbedder(dimensions=settings.jina_embedding_dimensions)
    # Imported here: the provider module implements this module's
    # interface, so a top-level import would be circular.
    from unstash.inference.jina import JinaEmbedder  # noqa: PLC0415

    return JinaEmbedder(
        api_key=settings.jina_api_key,
        base_url=settings.jina_base_url,
        model=settings.jina_embedding_model,
        dimensions=settings.jina_embedding_dimensions,
        timeout=settings.jina_timeout_seconds,
        http_client=http_client,
    )
