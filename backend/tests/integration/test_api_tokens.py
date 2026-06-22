"""Integration tests for API tokens: admin CRUD + Bearer dual-auth."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.conftest import (
    TEST_ADMIN_PASSWORD,
    TEST_APP_PASSWORD,
    TEST_MIGRATIONS_PASSWORD,
)
from unstash.auth.manager import _password_helper
from unstash.auth.tokens import hash_token
from unstash.config import get_settings
from unstash.db.engine import dispose_engine, get_admin_engine, get_engine
from unstash.db.session import get_admin_sessionmaker, get_sessionmaker
from unstash.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg


SUPERUSER_EMAIL = "admin@example.com"
USER_EMAIL = "alice@example.com"
SUPERUSER_PASSWORD = uuid.uuid4().hex + "Aa1"
USER_PASSWORD = uuid.uuid4().hex + "Aa1"


async def _seed_user(
    pool: asyncpg.Pool,
    email: str,
    password: str,
    *,
    is_superuser: bool,
) -> uuid.UUID:
    hashed = _password_helper().hash(password)
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO users (email, hashed_password, is_active, is_verified, is_superuser) "
            "VALUES ($1, $2, true, true, $3) RETURNING id",
            email,
            hashed,
            is_superuser,
        )


async def _seed_org(pool: asyncpg.Pool, slug: str, name: str) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO organisations (slug, name) VALUES ($1, $2) RETURNING id",
            slug,
            name,
        )


async def _seed_membership(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    role: str = "member",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO org_memberships (user_id, org_id, role) VALUES ($1, $2, $3)",
            user_id,
            org_id,
            role,
        )


@pytest.fixture
async def superuser_id(migrations_pool: asyncpg.Pool) -> uuid.UUID:
    return await _seed_user(
        migrations_pool,
        SUPERUSER_EMAIL,
        SUPERUSER_PASSWORD,
        is_superuser=True,
    )


@pytest.fixture
async def user_id(migrations_pool: asyncpg.Pool) -> uuid.UUID:
    return await _seed_user(
        migrations_pool,
        USER_EMAIL,
        USER_PASSWORD,
        is_superuser=False,
    )


@pytest.fixture
async def app_client(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> AsyncIterator[AsyncClient]:
    """ASGI client wired to the testcontainer."""
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


async def _login(client: AsyncClient, email: str, password: str) -> None:
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )
    assert response.status_code == 204, response.text


async def _create_token(
    client: AsyncClient,
    user_id: uuid.UUID,
    name: str = "test-token",
    expires_at: datetime | None = None,
) -> dict:
    """Helper: log in as superuser, mint a token, return the response body."""
    payload: dict = {"name": name}
    if expires_at is not None:
        payload["expires_at"] = expires_at.isoformat()
    response = await client.post(
        f"/api/admin/users/{user_id}/tokens",
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_token_returns_plaintext_once(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Plaintext is returned at creation; the list endpoint never repeats it."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    body = await _create_token(app_client, user_id, name="ingestion-bot")
    assert body["token"].startswith("uns_test_")
    assert body["name"] == "ingestion-bot"
    assert body["user_id"] == str(user_id)
    assert body["revoked_at"] is None

    listing = await app_client.get(f"/api/admin/users/{user_id}/tokens")
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert "token" not in rows[0]


async def test_token_authenticates_against_auth_me(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """A freshly minted Bearer token authenticates /api/auth/me as its owner."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    body = await _create_token(app_client, user_id)
    plaintext = body["token"]

    # Drop the cookie so we are unambiguously authenticating via Bearer.
    app_client.cookies.clear()
    response = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(user_id)


async def test_token_authenticates_against_org_route(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    user_id: uuid.UUID,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A Bearer token works on org-scoped routes too."""
    _ = superuser_id
    org_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_id, org_id)

    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    body = await _create_token(app_client, user_id)

    app_client.cookies.clear()
    response = await app_client.get(
        "/api/orgs/acme/me",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["user_id"] == str(user_id)


async def test_revoked_token_rejected(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """A revoked token returns 401."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    body = await _create_token(app_client, user_id)
    token_id = body["id"]

    revoke = await app_client.post(
        f"/api/admin/users/{user_id}/tokens/{token_id}/revoke",
    )
    assert revoke.status_code == 204

    app_client.cookies.clear()
    response = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert response.status_code == 401


async def test_expired_token_rejected(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """A token past its ``expires_at`` returns 401."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    body = await _create_token(
        app_client,
        user_id,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    app_client.cookies.clear()
    response = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert response.status_code == 401


async def test_malformed_bearer_rejected(
    app_client: AsyncClient,
    user_id: uuid.UUID,
) -> None:
    """A Bearer value that doesn't look like our token is 401, not silent-fallback to cookie."""
    _ = user_id
    response = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer some-random-string"},
    )
    assert response.status_code == 401


async def test_bearer_takes_priority_over_cookie(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """When a Bearer header is present, the cookie is ignored.

    The superuser logs in (sets a cookie), creates a token for a
    different user, then makes a request with the token's Bearer
    header. The cookie identifies the superuser; the token identifies
    the other user. The response must be the token's identity.
    """
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    body = await _create_token(app_client, user_id)

    # Cookie is still set in the client.
    response = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == str(user_id)


async def test_token_for_unknown_user_rejected(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    user_id: uuid.UUID,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A token whose user has been deleted returns 401."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    body = await _create_token(app_client, user_id)

    # Delete the user out from under the token (cascades nuke the token
    # row too, so this exercises the "row not found" branch).
    async with migrations_pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE id = $1", user_id)

    app_client.cookies.clear()
    response = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert response.status_code == 401


async def test_last_used_at_updates_on_use(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    user_id: uuid.UUID,
    migrations_pool: asyncpg.Pool,
) -> None:
    """``last_used_at`` is bumped after a successful Bearer auth."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    body = await _create_token(app_client, user_id)

    async with migrations_pool.acquire() as conn:
        before = await conn.fetchval(
            "SELECT last_used_at FROM api_tokens WHERE id = $1",
            body["id"],
        )
    assert before is None

    app_client.cookies.clear()
    response = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert response.status_code == 200

    async with migrations_pool.acquire() as conn:
        after = await conn.fetchval(
            "SELECT last_used_at FROM api_tokens WHERE id = $1",
            body["id"],
        )
    assert after is not None


async def test_token_storage_is_hash_not_plaintext(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    user_id: uuid.UUID,
    migrations_pool: asyncpg.Pool,
) -> None:
    """The DB row stores a SHA-256 digest, not the plaintext token."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    body = await _create_token(app_client, user_id)

    async with migrations_pool.acquire() as conn:
        stored_hash = await conn.fetchval(
            "SELECT token_hash FROM api_tokens WHERE id = $1",
            body["id"],
        )

    assert isinstance(stored_hash, bytes)
    assert len(stored_hash) == 32
    assert stored_hash == hash_token(body["token"])
    # Sanity: the stored value is not just the plaintext encoded.
    assert body["token"].encode("utf-8") != stored_hash


async def test_non_superuser_cannot_create_tokens(
    app_client: AsyncClient,
    user_id: uuid.UUID,
) -> None:
    """A regular logged-in user cannot mint tokens for anyone."""
    await _login(app_client, USER_EMAIL, USER_PASSWORD)
    response = await app_client.post(
        f"/api/admin/users/{user_id}/tokens",
        json={"name": "should-fail"},
    )
    assert response.status_code == 403
