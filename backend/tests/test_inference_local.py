"""Unit tests for the clients of the self-hosted inference service."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from unstash.config import Settings
from unstash.documents.embedder import EmbeddingError, EmbeddingTask, get_embedder
from unstash.inference import _http as http_module
from unstash.inference.local import LocalEmbedder, LocalReranker
from unstash.search.reranker import RerankError, get_reranker

if TYPE_CHECKING:
    from collections.abc import Callable

    Handler = Callable[[httpx.Request], httpx.Response]

pytestmark = pytest.mark.anyio

_BASE_URL = "http://inference.example:8000"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    original = httpx.AsyncClient

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(http_module.httpx, "AsyncClient", factory)


def _no_backoff(attempt: int) -> float:
    _ = attempt
    return 0.0


def _embedder(dimensions: int = 2) -> LocalEmbedder:
    return LocalEmbedder(base_url=_BASE_URL + "/", dimensions=dimensions, timeout=5.0)


def _reranker() -> LocalReranker:
    return LocalReranker(base_url=_BASE_URL, timeout=5.0)


# --- embedder ---------------------------------------------------------------


async def test_embed_sends_task_and_parses_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{_BASE_URL}/embed"
        assert "authorization" not in request.headers
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"vectors": [[1, 0], [0, 1]], "total_tokens": 7})

    _install_transport(monkeypatch, handler)
    batch = await _embedder().embed(["a", "b"], task=EmbeddingTask.QUERY)

    assert captured == [{"texts": ["a", "b"], "task": "query"}]
    assert batch.vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert batch.total_tokens == 7


async def test_embed_splits_large_inputs_and_keeps_order(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["texts"]
        sizes.append(len(texts))
        return httpx.Response(
            200,
            json={"vectors": [[float(text), 0.0] for text in texts], "total_tokens": len(texts)},
        )

    _install_transport(monkeypatch, handler)
    texts = [str(i) for i in range(150)]
    batch = await _embedder().embed(texts, task=EmbeddingTask.PASSAGE)

    assert sizes == [64, 64, 22]
    assert [vector[0] for vector in batch.vectors] == [float(i) for i in range(150)]
    assert batch.total_tokens == 150


async def test_embed_empty_input_skips_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    _install_transport(monkeypatch, handler)
    batch = await _embedder().embed([], task=EmbeddingTask.PASSAGE)
    assert batch.vectors == []
    assert batch.total_tokens == 0


async def test_embed_rejects_unexpected_width(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, json={"vectors": [[1.0, 0.0, 0.0]], "total_tokens": 1})

    _install_transport(monkeypatch, handler)
    with pytest.raises(EmbeddingError, match="width"):
        await _embedder(dimensions=2).embed(["a"], task=EmbeddingTask.PASSAGE)


async def test_embed_rejects_count_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, json={"vectors": [[1.0, 0.0]], "total_tokens": 1})

    _install_transport(monkeypatch, handler)
    with pytest.raises(EmbeddingError, match="1 vectors for 2 inputs"):
        await _embedder().embed(["a", "b"], task=EmbeddingTask.PASSAGE)


async def test_embed_rejects_malformed_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, json={"unexpected": True})

    _install_transport(monkeypatch, handler)
    with pytest.raises(EmbeddingError, match="shape"):
        await _embedder().embed(["a"], task=EmbeddingTask.PASSAGE)


async def test_embed_retries_transient_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_module, "_backoff_seconds", _no_backoff)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"vectors": [[1.0, 0.0]], "total_tokens": 1})

    _install_transport(monkeypatch, handler)
    batch = await _embedder().embed(["a"], task=EmbeddingTask.PASSAGE)
    assert calls["n"] == 2
    assert len(batch.vectors) == 1


# --- reranker ---------------------------------------------------------------


async def test_rerank_orders_by_score(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{_BASE_URL}/rerank"
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"scores": [0.1, 0.9, 0.5]})

    _install_transport(monkeypatch, handler)
    result = await _reranker().rerank("q", ["a", "b", "c"])

    assert captured == [{"query": "q", "documents": ["a", "b", "c"]}]
    assert result.order == [1, 2, 0]
    assert result.scores == [0.9, 0.5, 0.1]


async def test_rerank_empty_candidates_skips_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    _install_transport(monkeypatch, handler)
    result = await _reranker().rerank("q", [])
    assert result.order == []


async def test_rerank_rejects_count_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, json={"scores": [0.1]})

    _install_transport(monkeypatch, handler)
    with pytest.raises(RerankError, match="1 scores for 2 documents"):
        await _reranker().rerank("q", ["a", "b"])


async def test_rerank_rejects_malformed_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, json={"scores": ["not-a-number"]})

    _install_transport(monkeypatch, handler)
    with pytest.raises(RerankError, match="shape"):
        await _reranker().rerank("q", ["a"])


async def test_rerank_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_module, "_backoff_seconds", _no_backoff)

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(503)

    _install_transport(monkeypatch, handler)
    with pytest.raises(RerankError, match="after 2 attempts"):
        await _reranker().rerank("q", ["a"])


# --- configuration ----------------------------------------------------------


def test_factories_select_the_local_backends() -> None:
    settings = Settings(embedder_backend="local", reranker_backend="local")
    embedder = get_embedder(settings)
    assert isinstance(embedder, LocalEmbedder)
    assert embedder.embedding_dim == settings.inference_embedding_dimensions
    assert isinstance(get_reranker(settings), LocalReranker)


def test_derived_settings_follow_the_selected_backend() -> None:
    hosted = Settings(embedder_backend="jina", reranker_backend="jina")
    assert hosted.embedding_dimensions == hosted.jina_embedding_dimensions
    assert hosted.embedding_model == hosted.jina_embedding_model
    assert hosted.rerank_model == hosted.jina_rerank_model

    local = Settings(
        embedder_backend="local",
        reranker_backend="local",
        inference_embedding_dimensions=768,
    )
    assert local.embedding_dimensions == 768
    assert local.embedding_model == local.inference_embedding_model
    assert local.rerank_model == local.inference_rerank_model


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="embedder_backend"):
        Settings(embedder_backend="other")  # type: ignore[arg-type]
