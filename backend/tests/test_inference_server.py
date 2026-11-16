"""Unit tests for the inference service's HTTP layer, with a stub engine."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from unstash.inference_server.app import create_app
from unstash.inference_server.schemas import MAX_DOCUMENTS, MAX_TEXTS
from unstash.inference_server.settings import InferenceServerSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

pytestmark = pytest.mark.anyio

_BASE_URL = "http://inference.test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class StubEngine:
    """Records calls and returns values derived from the inputs."""

    def __init__(self) -> None:
        self.embed_calls: list[tuple[list[str], bool]] = []

    def embed(self, texts: list[str], *, is_query: bool) -> tuple[list[list[float]], int]:
        self.embed_calls.append((texts, is_query))
        return [[float(len(text)), 0.0] for text in texts], sum(len(text) for text in texts)

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [float(document.count(query)) for document in documents]


@pytest.fixture
def engine() -> StubEngine:
    return StubEngine()


@asynccontextmanager
async def _serving(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Run the app's startup, then yield a client bound to it."""
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url=_BASE_URL) as client,
    ):
        yield client


@pytest.fixture
async def client(engine: StubEngine) -> AsyncIterator[AsyncClient]:
    async with _serving(create_app(lambda: engine)) as test_client:
        yield test_client


async def test_health_reports_ok_once_started(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_engine_is_loaded_once_at_startup() -> None:
    loads = []

    def loader() -> StubEngine:
        loads.append(1)
        return StubEngine()

    async with _serving(create_app(loader)) as test_client:
        await test_client.get("/health")
        await test_client.post("/embed", json={"texts": ["a"], "task": "passage"})
    assert len(loads) == 1


async def test_embed_returns_vectors_in_input_order(
    client: AsyncClient, engine: StubEngine
) -> None:
    response = await client.post("/embed", json={"texts": ["a", "abc"], "task": "passage"})
    assert response.status_code == 200
    assert response.json() == {"vectors": [[1.0, 0.0], [3.0, 0.0]], "total_tokens": 4}
    assert engine.embed_calls == [(["a", "abc"], False)]


async def test_embed_marks_queries(client: AsyncClient, engine: StubEngine) -> None:
    await client.post("/embed", json={"texts": ["q"], "task": "query"})
    assert engine.embed_calls == [(["q"], True)]


@pytest.mark.parametrize(
    "body",
    [
        {"texts": [], "task": "passage"},
        {"texts": [""], "task": "passage"},
        {"texts": ["a"] * (MAX_TEXTS + 1), "task": "passage"},
        {"texts": ["a"], "task": "other"},
        {"texts": ["a"]},
    ],
)
async def test_embed_rejects_invalid_requests(client: AsyncClient, body: dict[str, object]) -> None:
    assert (await client.post("/embed", json=body)).status_code == 422


async def test_rerank_returns_scores_in_input_order(client: AsyncClient) -> None:
    response = await client.post(
        "/rerank",
        json={"query": "x", "documents": ["none", "x", "x and x"]},
    )
    assert response.status_code == 200
    assert response.json() == {"scores": [0.0, 1.0, 2.0]}


@pytest.mark.parametrize(
    "body",
    [
        {"query": "", "documents": ["a"]},
        {"query": "q", "documents": []},
        {"query": "q", "documents": ["a"] * (MAX_DOCUMENTS + 1)},
    ],
)
async def test_rerank_rejects_invalid_requests(
    client: AsyncClient, body: dict[str, object]
) -> None:
    assert (await client.post("/rerank", json=body)).status_code == 422


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNSTASH_INFERENCE_THREADS", "2")
    monkeypatch.setenv("UNSTASH_INFERENCE_EMBEDDING_MODEL", "example/model")
    settings = InferenceServerSettings()
    assert settings.threads == 2
    assert settings.embedding_model == "example/model"
