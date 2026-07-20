"""Unit tests for the hosted HF NER endpoint client."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from unstash.documents.ner import ExtractedEntity
from unstash.inference import hf_ner as hf_module
from unstash.inference.hf_ner import HfEndpointExtractor, NerError

if TYPE_CHECKING:
    from collections.abc import Callable

    Handler = Callable[[httpx.Request], httpx.Response]


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    original = httpx.AsyncClient

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(hf_module.httpx, "AsyncClient", factory)


def _no_backoff(attempt: int) -> float:
    _ = attempt
    return 0.0


def _extractor(min_score: float = 0.7) -> HfEndpointExtractor:
    return HfEndpointExtractor(
        endpoint_url="https://ner.example/endpoint",
        api_key="test-key",
        min_score=min_score,
        timeout=5.0,
    )


async def test_maps_and_filters_endpoint_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json=[
                {"entity_group": "PER", "word": "Anna Svensson", "score": 0.99},
                {"entity_group": "LOC", "word": "Stockholm", "score": 0.98},
                {"entity_group": "OBJ", "word": "Volvo", "score": 0.99},  # dropped
                {"entity_group": "PER", "word": "Osäker", "score": 0.30},  # below floor
            ],
        )

    _install_transport(monkeypatch, handler)
    entities = await _extractor().extract("...")
    assert entities == [
        ExtractedEntity(text="Anna Svensson", label="person", score=0.99),
        ExtractedEntity(text="Stockholm", label="location", score=0.98),
    ]


async def test_empty_text_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected for empty text")

    _install_transport(monkeypatch, handler)
    assert await _extractor().extract("   ") == []


async def test_retries_through_cold_start_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hf_module, "_backoff_seconds", _no_backoff)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)  # replica cold-starting
        return httpx.Response(200, json=[{"entity_group": "PER", "word": "Erik", "score": 0.9}])

    _install_transport(monkeypatch, handler)
    entities = await _extractor().extract("...")
    assert [e.text for e in entities] == ["Erik"]
    assert calls["n"] == 2


async def test_non_retryable_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(400, text="bad request")

    _install_transport(monkeypatch, handler)
    with pytest.raises(NerError, match="400"):
        await _extractor().extract("...")


async def test_missing_endpoint_url_raises() -> None:
    extractor = HfEndpointExtractor(endpoint_url="", api_key="k", min_score=0.7, timeout=5.0)
    with pytest.raises(NerError, match="not configured"):
        await extractor.extract("...")
