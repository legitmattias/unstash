"""Tests for the health endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from unstash.__about__ import __version__

if TYPE_CHECKING:
    from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


async def test_health_response_is_json(client: AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.headers["content-type"] == "application/json"
