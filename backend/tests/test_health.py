"""Tests for the health and readiness endpoints."""

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


async def test_ready_returns_503_when_database_unreachable(
    client: AsyncClient,
) -> None:
    """Without a real database, the readiness probe should report not-ready.

    The test settings configure the engine with a fake DSN that cannot reach
    any real postgres, so the SELECT 1 ping will fail with a SQLAlchemyError
    and the endpoint should translate that into a 503.
    """
    response = await client.get("/api/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["detail"]["status"] == "not ready"
