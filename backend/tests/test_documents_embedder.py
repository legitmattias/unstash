"""Tests for the embedding clients.

The fake is verified for determinism and shape; the Jina client is
verified against a mocked HTTP layer (request payload, response parsing,
retry-then-succeed, and error translation) with no network.
"""

from __future__ import annotations

import json
import math
import secrets
from collections.abc import Callable

import httpx
import pytest

from unstash.config import Settings
from unstash.documents.embedder import (
    EmbeddingError,
    EmbeddingTask,
    FakeEmbedder,
    get_embedder,
)
from unstash.inference import jina as jina_module
from unstash.inference.jina import JinaEmbedder

Handler = Callable[[httpx.Request], httpx.Response]

# Generated per run; no literal for secret scanners to match.
_TEST_API_KEY = secrets.token_urlsafe(16)


def _no_backoff(_attempt: int) -> float:
    return 0.0


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(jina_module.httpx, "AsyncClient", factory)


# --- FakeEmbedder ----------------------------------------------------------


async def test_fake_embedder_is_deterministic_and_normalised() -> None:
    fake = FakeEmbedder(dimensions=16)

    first = await fake.embed(["styrelseprotokoll"], task=EmbeddingTask.PASSAGE)
    second = await fake.embed(["styrelseprotokoll"], task=EmbeddingTask.PASSAGE)

    assert first.vectors == second.vectors
    (vector,) = first.vectors
    assert len(vector) == 16
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-6)


async def test_fake_embedder_distinguishes_texts() -> None:
    fake = FakeEmbedder(dimensions=16)
    batch = await fake.embed(["taket", "ekonomi"], task=EmbeddingTask.PASSAGE)
    assert batch.vectors[0] != batch.vectors[1]
    assert batch.total_tokens > 0


async def test_fake_embedder_empty_input() -> None:
    fake = FakeEmbedder(dimensions=16)
    batch = await fake.embed([], task=EmbeddingTask.PASSAGE)
    assert batch.vectors == []
    assert batch.total_tokens == 0


# --- JinaEmbedder ----------------------------------------------------------


def _jina() -> JinaEmbedder:
    return JinaEmbedder(
        api_key=_TEST_API_KEY,
        base_url="https://api.jina.ai/v1",
        model="jina-embeddings-v4",
        dimensions=3,
        timeout=5.0,
        max_retries=2,
    )


async def test_jina_sends_expected_request_and_parses_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        # Return out of order to prove the client re-orders by index.
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                ],
                "usage": {"total_tokens": 7},
            },
        )

    _install_transport(monkeypatch, handler)

    batch = await _jina().embed(["a", "b"], task=EmbeddingTask.PASSAGE)

    assert batch.vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert batch.total_tokens == 7
    assert captured["url"] == "https://api.jina.ai/v1/embeddings"
    assert captured["auth"] == f"Bearer {_TEST_API_KEY}"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "jina-embeddings-v4"
    assert body["task"] == "retrieval.passage"
    assert body["dimensions"] == 3
    assert body["input"] == [{"text": "a"}, {"text": "b"}]


async def test_jina_empty_input_skips_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        pytest.fail("no HTTP call expected for empty input")

    _install_transport(monkeypatch, handler)
    batch = await _jina().embed([], task=EmbeddingTask.PASSAGE)
    assert batch.vectors == []


async def test_jina_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jina_module, "_backoff_seconds", _no_backoff)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}], "usage": {}},
        )

    _install_transport(monkeypatch, handler)
    batch = await _jina().embed(["x"], task=EmbeddingTask.PASSAGE)

    assert calls["n"] == 2
    assert batch.vectors == [[1.0, 0.0, 0.0]]
    assert batch.total_tokens == 0


async def test_jina_non_retryable_status_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    _install_transport(monkeypatch, handler)
    with pytest.raises(EmbeddingError) as excinfo:
        await _jina().embed(["x"], task=EmbeddingTask.PASSAGE)
    assert "400" in str(excinfo.value)


async def test_jina_gives_up_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jina_module, "_backoff_seconds", _no_backoff)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    _install_transport(monkeypatch, handler)
    with pytest.raises(EmbeddingError):
        await _jina().embed(["x"], task=EmbeddingTask.PASSAGE)


async def test_jina_vector_count_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}], "usage": {}},
        )

    _install_transport(monkeypatch, handler)
    with pytest.raises(EmbeddingError):
        await _jina().embed(["a", "b"], task=EmbeddingTask.PASSAGE)


# --- factory ---------------------------------------------------------------


def test_get_embedder_selects_backend() -> None:
    fake_settings = Settings(embedder_backend="fake", jina_embedding_dimensions=32)
    assert isinstance(get_embedder(fake_settings), FakeEmbedder)
    assert get_embedder(fake_settings).embedding_dim == 32

    jina_settings = Settings(embedder_backend="jina")
    assert isinstance(get_embedder(jina_settings), JinaEmbedder)
