"""Admin endpoint integration tests — superuser gating, user CRUD, memberships."""

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


SUPERUSER_EMAIL = "admin@example.com"
REGULAR_USER_EMAIL = "alice@example.com"
SUPERUSER_PASSWORD = uuid.uuid4().hex + "Aa1"
REGULAR_USER_PASSWORD = uuid.uuid4().hex + "Aa1"
NEW_USER_PASSWORD = uuid.uuid4().hex + "Aa1"


async def _seed_user(
    pool: asyncpg.Pool,
    email: str,
    password: str,
    *,
    is_superuser: bool,
) -> uuid.UUID:
    """Insert a user with a known password and return its id."""
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
    """Insert an organisation and return its id."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO organisations (slug, name) VALUES ($1, $2) RETURNING id",
            slug,
            name,
        )


@pytest.fixture
async def superuser_id(migrations_pool: asyncpg.Pool) -> uuid.UUID:
    """Seed a superuser available for admin-route tests."""
    return await _seed_user(
        migrations_pool,
        SUPERUSER_EMAIL,
        SUPERUSER_PASSWORD,
        is_superuser=True,
    )


@pytest.fixture
async def regular_user_id(migrations_pool: asyncpg.Pool) -> uuid.UUID:
    """Seed a non-superuser used as the subject of admin operations."""
    return await _seed_user(
        migrations_pool,
        REGULAR_USER_EMAIL,
        REGULAR_USER_PASSWORD,
        is_superuser=False,
    )


@pytest.fixture
async def org_id(migrations_pool: asyncpg.Pool) -> uuid.UUID:
    """Seed an organisation for membership tests."""
    return await _seed_org(migrations_pool, "acme", "Acme")


@pytest.fixture
async def app_client(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> AsyncIterator[AsyncClient]:
    """ASGI client wired to the testcontainer. No cookie pre-set."""
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


async def test_create_user_unauthenticated_rejected(
    app_client: AsyncClient,
) -> None:
    """Anonymous POST to admin endpoint returns 401."""
    response = await app_client.post(
        "/api/admin/users",
        json={"email": "new@example.com", "password": NEW_USER_PASSWORD},
    )
    assert response.status_code == 401


async def test_create_user_regular_user_rejected(
    app_client: AsyncClient,
    regular_user_id: uuid.UUID,
) -> None:
    """Authenticated non-superuser POST returns 403."""
    _ = regular_user_id
    await _login(app_client, REGULAR_USER_EMAIL, REGULAR_USER_PASSWORD)
    response = await app_client.post(
        "/api/admin/users",
        json={"email": "new@example.com", "password": NEW_USER_PASSWORD},
    )
    assert response.status_code == 403


async def test_create_user_succeeds_as_superuser(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
) -> None:
    """Superuser creates a user and gets back the created record."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    response = await app_client.post(
        "/api/admin/users",
        json={
            "email": "new@example.com",
            "password": NEW_USER_PASSWORD,
            "is_superuser": False,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["is_superuser"] is False


async def test_create_user_conflict_on_duplicate_email(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    regular_user_id: uuid.UUID,
) -> None:
    """Creating a user with an existing email returns 409."""
    _ = superuser_id, regular_user_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    response = await app_client.post(
        "/api/admin/users",
        json={"email": REGULAR_USER_EMAIL, "password": NEW_USER_PASSWORD},
    )
    assert response.status_code == 409


async def test_create_user_weak_password_rejected(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
) -> None:
    """Password shorter than 8 characters is rejected upfront by the schema."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    response = await app_client.post(
        "/api/admin/users",
        json={"email": "short@example.com", "password": "abc"},
    )
    assert response.status_code == 422


async def test_list_users_returns_seeded_users(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    regular_user_id: uuid.UUID,
) -> None:
    """List endpoint returns both seeded users."""
    _ = regular_user_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    response = await app_client.get("/api/admin/users")
    assert response.status_code == 200
    emails = {row["email"] for row in response.json()}
    assert SUPERUSER_EMAIL in emails
    assert REGULAR_USER_EMAIL in emails
    ids = {row["id"] for row in response.json()}
    assert str(superuser_id) in ids


async def test_delete_user_succeeds(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    regular_user_id: uuid.UUID,
) -> None:
    """Superuser can delete another user."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    response = await app_client.delete(f"/api/admin/users/{regular_user_id}")
    assert response.status_code == 204

    follow_up = await app_client.get("/api/admin/users")
    emails = {row["email"] for row in follow_up.json()}
    assert REGULAR_USER_EMAIL not in emails


async def test_delete_user_not_found(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
) -> None:
    """Deleting a missing user returns 404."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    response = await app_client.delete(f"/api/admin/users/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_add_membership_succeeds(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    regular_user_id: uuid.UUID,
    org_id: uuid.UUID,
) -> None:
    """Superuser adds a regular user to an org with a role."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    response = await app_client.post(
        f"/api/admin/users/{regular_user_id}/memberships",
        json={"org_id": str(org_id), "role": "member"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_id"] == str(regular_user_id)
    assert body["org_id"] == str(org_id)
    assert body["role"] == "member"


async def test_add_membership_conflict_on_duplicate(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    regular_user_id: uuid.UUID,
    org_id: uuid.UUID,
) -> None:
    """Re-adding the same (user, org) pair returns 409."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    first = await app_client.post(
        f"/api/admin/users/{regular_user_id}/memberships",
        json={"org_id": str(org_id), "role": "member"},
    )
    assert first.status_code == 201
    second = await app_client.post(
        f"/api/admin/users/{regular_user_id}/memberships",
        json={"org_id": str(org_id), "role": "admin"},
    )
    assert second.status_code == 409


async def test_add_membership_user_not_found(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    org_id: uuid.UUID,
) -> None:
    """Adding membership for an unknown user returns 404."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    response = await app_client.post(
        f"/api/admin/users/{uuid.uuid4()}/memberships",
        json={"org_id": str(org_id), "role": "member"},
    )
    assert response.status_code == 404


async def test_remove_membership_succeeds(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    regular_user_id: uuid.UUID,
    org_id: uuid.UUID,
) -> None:
    """Superuser removes a user's membership."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    create = await app_client.post(
        f"/api/admin/users/{regular_user_id}/memberships",
        json={"org_id": str(org_id), "role": "member"},
    )
    assert create.status_code == 201
    remove = await app_client.delete(
        f"/api/admin/users/{regular_user_id}/memberships/{org_id}",
    )
    assert remove.status_code == 204


async def test_remove_membership_not_found(
    app_client: AsyncClient,
    superuser_id: uuid.UUID,
    regular_user_id: uuid.UUID,
    org_id: uuid.UUID,
) -> None:
    """Removing a non-existent membership returns 404."""
    _ = superuser_id
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    response = await app_client.delete(
        f"/api/admin/users/{regular_user_id}/memberships/{org_id}",
    )
    assert response.status_code == 404
