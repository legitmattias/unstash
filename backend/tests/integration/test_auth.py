"""Auth integration tests — login, /me, logout against a real database."""

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
from unstash.auth.manager import _password_helper
from unstash.config import get_settings
from unstash.db.engine import dispose_engine, get_admin_engine, get_engine
from unstash.db.session import get_admin_sessionmaker, get_sessionmaker
from unstash.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg


TEST_EMAIL = "alice@example.com"
TEST_PASSWORD = uuid.uuid4().hex + "Aa1"


@pytest.fixture
async def seeded_user_id(migrations_pool: asyncpg.Pool) -> uuid.UUID:
    """Seed a user with a known password via direct DB write."""
    hashed = _password_helper().hash(TEST_PASSWORD)
    async with migrations_pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO users (email, hashed_password, is_active, is_verified) "
            "VALUES ($1, $2, true, true) RETURNING id",
            TEST_EMAIL,
            hashed,
        )


@pytest.fixture
async def client(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> AsyncIterator[AsyncClient]:
    """ASGI test client wired to the testcontainer database."""
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
    get_settings.cache_clear()
    # Engine and sessionmaker are lru_cached; clear so they're rebuilt against
    # the current test's event loop. Otherwise asyncpg complains that its
    # futures are attached to a different loop.
    get_engine.cache_clear()
    get_admin_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_admin_sessionmaker.cache_clear()

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        await dispose_engine()


async def test_login_returns_session_cookie(
    client: AsyncClient,
    seeded_user_id: uuid.UUID,
) -> None:
    """Login with correct credentials sets a session cookie."""
    _ = seeded_user_id
    response = await client.post(
        "/api/auth/login",
        data={"username": TEST_EMAIL, "password": TEST_PASSWORD},
    )

    assert response.status_code == 204
    assert "unstash_session" in response.cookies


async def test_me_requires_session(client: AsyncClient) -> None:
    """Unauthenticated /me returns 401."""
    response = await client.get("/api/auth/me")
    assert response.status_code == 401


async def test_me_returns_user_with_valid_session(
    client: AsyncClient,
    seeded_user_id: uuid.UUID,
) -> None:
    """/me with a valid cookie returns the authenticated user."""
    login = await client.post(
        "/api/auth/login",
        data={"username": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert login.status_code == 204

    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == TEST_EMAIL
    assert body["id"] == str(seeded_user_id)


async def test_logout_invalidates_session(
    client: AsyncClient,
    seeded_user_id: uuid.UUID,
) -> None:
    """After logout, the previously-valid cookie no longer authenticates."""
    _ = seeded_user_id
    login = await client.post(
        "/api/auth/login",
        data={"username": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert login.status_code == 204

    # Sanity check: /me works before logout.
    assert (await client.get("/api/auth/me")).status_code == 200

    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204

    # After logout, /me with the same cookie should be rejected.
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_wrong_password_rejected(
    client: AsyncClient,
    seeded_user_id: uuid.UUID,
) -> None:
    """Wrong password returns 400 (FastAPI-Users default for bad credentials)."""
    _ = seeded_user_id
    response = await client.post(
        "/api/auth/login",
        data={"username": TEST_EMAIL, "password": "wrong-password"},
    )
    assert response.status_code == 400


async def test_login_unknown_user_rejected(client: AsyncClient) -> None:
    """Unknown email returns 400."""
    response = await client.post(
        "/api/auth/login",
        data={"username": "ghost@example.com", "password": "irrelevant"},
    )
    assert response.status_code == 400
