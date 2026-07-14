"""Live Jina embedding test — opt-in, hits the real API.

Skipped unless ``JINA_API_KEY`` is set. It verifies the real API accepts
our exact request shape (``input:[{"text":…}]``, ``task:retrieval.passage``,
``dimensions``) — which the mocked unit tests cannot — so a change in the
Jina contract surfaces here rather than in production.
"""

from __future__ import annotations

import os

import pytest

from unstash.documents.embedder import EmbeddingTask, JinaEmbedder

_API_KEY = os.environ.get("JINA_API_KEY")

pytestmark = pytest.mark.skipif(
    not _API_KEY,
    reason="JINA_API_KEY not set; live Jina test is opt-in",
)


async def test_jina_live_embeds_passages() -> None:
    # 128-dim (Matryoshka-truncated) keeps the live response small.
    embedder = JinaEmbedder(
        api_key=_API_KEY or "",
        base_url="https://api.jina.ai/v1",
        model="jina-embeddings-v4",
        dimensions=128,
        timeout=30.0,
    )

    batch = await embedder.embed(
        [
            "Styrelsen beslutade att renovera taket på fastigheten.",
            "Annual financial report and budget.",
        ],
        task=EmbeddingTask.PASSAGE,
    )

    assert len(batch.vectors) == 2
    assert all(len(vector) == 128 for vector in batch.vectors)
    assert any(abs(value) > 0 for value in batch.vectors[0])
    assert batch.total_tokens > 0
