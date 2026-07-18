"""The login endpoint is rate-limited per client IP."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.conftest import (
    TEST_ADMIN_PASSWORD,
    TEST_APP_PASSWORD,
    TEST_MIGRATIONS_PASSWORD,
)
from unstash.config import get_settings
from unstash.db.engine import dispose_engine, get_admin_engine, get_engine
from unstash.db.session import get_admin_sessionmaker, get_sessionmaker
from unstash.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_MAX_ATTEMPTS = 3


class _FakeRedis:
    """In-memory INCR/EXPIRE stand-in for the rate-limit store."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, name: str) -> int:
        self.counts[name] = self.counts.get(name, 0) + 1
        return self.counts[name]

    async def expire(self, name: str, time: int) -> bool:
        _ = name, time
        return True

    async def aclose(self) -> None:
        return None


@pytest.fixture
async def client(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> AsyncIterator[AsyncClient]:
    """ASGI client with a fake Redis and a low login-attempt limit."""
    _ = migrated_database
    host, port = container_host_port
    monkeypatch.setenv("UNSTASH_DATABASE_HOST", host)
    monkeypatch.setenv("UNSTASH_DATABASE_PORT", str(port))
    monkeypatch.setenv("UNSTASH_DATABASE_NAME", "unstash")
    monkeypatch.setenv("UNSTASH_DATABASE_USER", "unstash_app")
    monkeypatch.setenv("database_password", TEST_APP_PASSWORD)
    monkeypatch.setenv("database_migrations_password", TEST_MIGRATIONS_PASSWORD)
    monkeypatch.setenv("database_admin_password", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("session_secret", uuid.uuid4().hex)
    monkeypatch.setenv("encryption_key", uuid.uuid4().hex)
    monkeypatch.setenv("UNSTASH_ENVIRONMENT", "test")
    monkeypatch.setenv("UNSTASH_LOGIN_RATE_LIMIT_MAX_ATTEMPTS", str(_MAX_ATTEMPTS))
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_admin_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_admin_sessionmaker.cache_clear()

    app = create_app()
    # Lifespan does not run under ASGITransport; inject the store the
    # middleware reads so the limiter is active for the test.
    app.state.redis = _FakeRedis()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        await dispose_engine()


async def test_login_is_rate_limited_per_ip(client: AsyncClient) -> None:
    """Attempts up to the limit hit the handler; the next is a 429."""
    creds = {"username": "nobody@example.com", "password": "wrong-password"}

    for _ in range(_MAX_ATTEMPTS):
        response = await client.post("/api/auth/login", data=creds)
        assert response.status_code != 429, response.text

    blocked = await client.post("/api/auth/login", data=creds)
    assert blocked.status_code == 429
    assert "Too many" in blocked.json()["detail"]


async def test_rate_limit_keys_on_forwarded_ip(client: AsyncClient) -> None:
    """A different X-Forwarded-For client gets its own budget."""
    creds = {"username": "nobody@example.com", "password": "wrong-password"}

    for _ in range(_MAX_ATTEMPTS + 1):
        await client.post(
            "/api/auth/login",
            data=creds,
            headers={"X-Forwarded-For": "10.0.0.1"},
        )
    # The first client is now blocked; a second forwarded IP is not.
    blocked = await client.post(
        "/api/auth/login",
        data=creds,
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert blocked.status_code == 429
    fresh = await client.post(
        "/api/auth/login",
        data=creds,
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert fresh.status_code != 429
