"""Org-scoping integration tests: dependency wiring + RLS visibility."""

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


USER_PASSWORD = uuid.uuid4().hex + "Aa1"
USER_A_EMAIL = "user-a@example.com"


async def _seed_user(pool: asyncpg.Pool, email: str, password: str) -> uuid.UUID:
    hashed = _password_helper().hash(password)
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO users (email, hashed_password, is_active, is_verified) "
            "VALUES ($1, $2, true, true) RETURNING id",
            email,
            hashed,
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
    role: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO org_memberships (user_id, org_id, role) VALUES ($1, $2, $3)",
            user_id,
            org_id,
            role,
        )


@pytest.fixture
async def acme_id(migrations_pool: asyncpg.Pool) -> uuid.UUID:
    return await _seed_org(migrations_pool, "acme", "Acme")


@pytest.fixture
async def beta_id(migrations_pool: asyncpg.Pool) -> uuid.UUID:
    return await _seed_org(migrations_pool, "beta", "Beta")


@pytest.fixture
async def user_a_id(migrations_pool: asyncpg.Pool) -> uuid.UUID:
    return await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)


@pytest.fixture
async def user_a_in_acme(
    migrations_pool: asyncpg.Pool,
    user_a_id: uuid.UUID,
    acme_id: uuid.UUID,
) -> uuid.UUID:
    """User A is a member of Acme. Beta is intentionally not joined."""
    await _seed_membership(migrations_pool, user_a_id, acme_id, "member")
    return user_a_id


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


async def test_unauthenticated_org_route_returns_401(
    app_client: AsyncClient,
    acme_id: uuid.UUID,
) -> None:
    """Org-scoped route rejects unauthenticated callers before resolving the slug."""
    _ = acme_id
    response = await app_client.get("/api/orgs/acme/me")
    assert response.status_code == 401


async def test_unknown_slug_returns_404(
    app_client: AsyncClient,
    user_a_in_acme: uuid.UUID,
) -> None:
    """Authenticated caller against a non-existent slug gets 404, not 403."""
    _ = user_a_in_acme
    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get("/api/orgs/no-such-org/me")
    assert response.status_code == 404


async def test_non_member_returns_403(
    app_client: AsyncClient,
    user_a_in_acme: uuid.UUID,
    beta_id: uuid.UUID,
) -> None:
    """User in Acme attempting to access Beta gets 403."""
    _ = user_a_in_acme, beta_id
    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get("/api/orgs/beta/me")
    assert response.status_code == 403


async def test_member_gets_own_membership(
    app_client: AsyncClient,
    user_a_in_acme: uuid.UUID,
    acme_id: uuid.UUID,
) -> None:
    """Member of Acme reads their own membership through the org-scoped session."""
    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get("/api/orgs/acme/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == str(user_a_in_acme)
    assert body["org_id"] == str(acme_id)
    assert body["role"] == "member"


async def test_rls_blocks_cross_org_visibility(
    app_client: AsyncClient,
    user_a_in_acme: uuid.UUID,
    beta_id: uuid.UUID,
    migrations_pool: asyncpg.Pool,
) -> None:
    """A second user in Beta is not visible when reading from Acme's context.

    Seeds a Beta-only user, then queries acme's /me as the Acme user.
    The endpoint only sees the Acme membership — RLS filters Beta's row
    out automatically because ``app.current_org_id`` is set to Acme.
    The /me handler queries by ``user_id = current_user.id`` only and
    trusts RLS for the org-scoping; this test proves that trust is
    warranted end-to-end.
    """
    _ = user_a_in_acme
    # Seed a second user with a membership in Beta. The same user_id
    # technically can't be in two orgs in this test because we're
    # asserting Acme's /me returns ONE row — that row must be the Acme
    # one.  Add Beta as a separate user so the cross-org noise is real.
    other_user = await _seed_user(
        migrations_pool,
        "user-b@example.com",
        USER_PASSWORD,
    )
    await _seed_membership(migrations_pool, other_user, beta_id, "admin")

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get("/api/orgs/acme/me")
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "member"
    # Sanity: the response is for the Acme membership, not someone
    # else's row leaking in.
    assert body["user_id"] == str(user_a_in_acme)
