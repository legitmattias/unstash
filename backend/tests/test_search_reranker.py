"""Unit tests for the rerank client (fake and Jina)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from unstash.inference import jina as jina_module
from unstash.inference.jina import JinaReranker
from unstash.search.reranker import FakeReranker, RerankError

if TYPE_CHECKING:
    from collections.abc import Callable

    Handler = Callable[[httpx.Request], httpx.Response]

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    original = httpx.AsyncClient

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(jina_module.httpx, "AsyncClient", factory)


def _no_backoff(attempt: int) -> float:
    _ = attempt
    return 0.0


def _client() -> JinaReranker:
    return JinaReranker(
        api_key="test-key",
        base_url="https://rerank.example/v1",
        model="test-model",
        timeout=5.0,
    )


async def test_fake_reranker_prefers_overlap() -> None:
    result = await FakeReranker().rerank(
        "taket beslut",
        ["om ekonomi och budget", "beslut om taket", "trädgården"],
    )
    assert result.order[0] == 1
    assert len(result.order) == 3
    assert result.scores[0] > result.scores[-1]


async def test_fake_reranker_is_deterministic() -> None:
    docs = ["alpha beta", "gamma delta", "beta gamma"]
    first = await FakeReranker().rerank("beta", docs)
    second = await FakeReranker().rerank("beta", docs)
    assert first.order == second.order
    assert first.scores == second.scores


async def test_fake_reranker_empty_input() -> None:
    result = await FakeReranker().rerank("anything", [])
    assert result.order == []
    assert result.scores == []


async def test_jina_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ],
            },
        )

    _install_transport(monkeypatch, handler)
    result = await _client().rerank("q", ["a", "b"])
    assert result.order == [1, 0]
    assert result.scores == [0.9, 0.2]
    assert captured["model"] == "test-model"
    assert captured["top_n"] == 2


async def test_jina_empty_input_skips_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    _install_transport(monkeypatch, handler)
    result = await _client().rerank("q", [])
    assert result.order == []


async def test_jina_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jina_module, "_backoff_seconds", _no_backoff)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 1.0}]},
        )

    _install_transport(monkeypatch, handler)
    result = await _client().rerank("q", ["a"])
    assert result.order == [0]
    assert calls["n"] == 2


async def test_jina_non_retryable_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(400, text="bad request")

    _install_transport(monkeypatch, handler)
    with pytest.raises(RerankError, match="400"):
        await _client().rerank("q", ["a"])


async def test_jina_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jina_module, "_backoff_seconds", _no_backoff)

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(503)

    _install_transport(monkeypatch, handler)
    with pytest.raises(RerankError, match="after"):
        await _client().rerank("q", ["a"])


async def test_jina_incomplete_coverage_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 1.0}]},
        )

    _install_transport(monkeypatch, handler)
    with pytest.raises(RerankError, match="cover"):
        await _client().rerank("q", ["a", "b"])
