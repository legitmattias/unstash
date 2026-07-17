"""The Jina clients reuse an injected HTTP client and never close it."""

from __future__ import annotations

import httpx
import pytest

from unstash.documents.embedder import EmbeddingTask
from unstash.inference.jina import JinaEmbedder, JinaReranker

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _embed_response(request: httpx.Request) -> httpx.Response:
    _ = request
    return httpx.Response(
        200,
        json={"data": [{"index": 0, "embedding": [0.1, 0.2]}], "usage": {"total_tokens": 3}},
    )


def _rerank_response(request: httpx.Request) -> httpx.Response:
    _ = request
    return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})


async def test_embedder_uses_injected_client_and_leaves_it_open() -> None:
    shared = httpx.AsyncClient(transport=httpx.MockTransport(_embed_response))
    embedder = JinaEmbedder(
        api_key="k",
        base_url="https://embed.example/v1",
        model="m",
        dimensions=2,
        timeout=5.0,
        http_client=shared,
    )
    first = await embedder.embed(["a"], task=EmbeddingTask.QUERY)
    second = await embedder.embed(["b"], task=EmbeddingTask.QUERY)
    assert first.vectors == second.vectors == [[0.1, 0.2]]
    assert not shared.is_closed
    await shared.aclose()


async def test_reranker_uses_injected_client_and_leaves_it_open() -> None:
    shared = httpx.AsyncClient(transport=httpx.MockTransport(_rerank_response))
    reranker = JinaReranker(
        api_key="k",
        base_url="https://rerank.example/v1",
        model="m",
        timeout=5.0,
        http_client=shared,
    )
    result = await reranker.rerank("q", ["doc"])
    assert result.order == [0]
    assert not shared.is_closed
    await shared.aclose()


async def test_embedder_without_injected_client_still_works() -> None:
    embedder = JinaEmbedder(
        api_key="k",
        base_url="https://embed.example/v1",
        model="m",
        dimensions=2,
        timeout=5.0,
    )
    # No injected client and no mock transport: the per-call client path
    # is exercised by the existing embedder unit tests; here we only
    # assert construction stays valid without the parameter.
    assert embedder.embedding_dim == 2
