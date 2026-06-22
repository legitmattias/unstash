"""Adversarial auth tests — the cross-layer probes that close M2.5-C.

The existing test files cover their specific surfaces in isolation:

- ``test_auth.py`` — login / logout / /me on a single user.
- ``test_orgs.py`` — org-scoping dependency in isolation, plus single-row
  RLS visibility from the route layer.
- ``test_api_tokens.py`` — token lifecycle and Bearer auth round-trip.
- ``test_rls.py`` — Row-Level Security at the database layer directly.

This file fills the gaps where those surfaces meet adversarially. The
intent is to make a cross-tenant data leak impossible to ship by
ensuring every combination of auth-state, org-context, and auth-mode
is exercised end-to-end against a real Postgres.

Specifically, this suite covers:

1. **Full RLS scoping through the route.** A user who is a member of
   two orgs sees only the current-org membership row when querying via
   ``/api/orgs/<slug>/me`` — RLS does the work; the route does not
   filter by org_id manually.
2. **Concurrent sessions are independent.** Logging out one session
   does not invalidate another.
3. **Bearer and cookie produce identical visibility.** The auth mode
   is invisible at the route layer — the same user gets the same body
   regardless of how they authenticated.
4. **Token revocation blocks org routes too.** Not just /api/auth/me.
5. **Logged-out cookie cannot access org routes.** Session invalidation
   applies uniformly across the auth surface.
"""

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
USER_A_EMAIL = "alice@example.com"
USER_B_EMAIL = "bob@example.com"
SUPERUSER_PASSWORD = uuid.uuid4().hex + "Aa1"
USER_PASSWORD = uuid.uuid4().hex + "Aa1"


async def _seed_user(
    pool: asyncpg.Pool,
    email: str,
    password: str,
    *,
    is_superuser: bool = False,
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
) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO org_memberships (user_id, org_id, role) VALUES ($1, $2, $3) RETURNING id",
            user_id,
            org_id,
            role,
        )


def _set_test_env(monkeypatch: pytest.MonkeyPatch, host: str, port: int) -> None:
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


@pytest.fixture
async def app_client(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> AsyncIterator[AsyncClient]:
    """ASGI client wired to the testcontainer database."""
    _ = migrated_database
    host, port = container_host_port
    _set_test_env(monkeypatch, host, port)

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


async def _create_token_as_admin(client: AsyncClient, user_id: uuid.UUID) -> str:
    """Helper: requires the client to already be logged in as a superuser."""
    response = await client.post(
        f"/api/admin/users/{user_id}/tokens",
        json={"name": "adversarial-test-token"},
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]


async def test_rls_scopes_through_route_for_multi_org_user(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Same user in two orgs sees only the current-org membership via the route.

    User A is a member of both Acme and Beta. They have two rows in
    ``org_memberships``. When they call ``GET /api/orgs/acme/me``, the
    route's handler queries ``WHERE user_id = current_user.id`` and
    does not filter by org_id explicitly — RLS does that, scoped to
    ``app.current_org_id`` (set by the org-scoping dependency to the
    Acme UUID).

    If RLS were misconfigured or the dependency forgot to set the GUC,
    the handler would return *both* membership rows and (per the
    ``scalar_one_or_none`` shape) fail or return the wrong one. The
    test asserts that the returned membership has the Acme role, not
    the Beta role.
    """
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    beta_id = await _seed_org(migrations_pool, "beta", "Beta")
    await _seed_membership(migrations_pool, user_a, acme_id, "member")
    await _seed_membership(migrations_pool, user_a, beta_id, "admin")

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)

    acme_response = await app_client.get("/api/orgs/acme/me")
    assert acme_response.status_code == 200, acme_response.text
    assert acme_response.json()["role"] == "member"
    assert acme_response.json()["org_id"] == str(acme_id)

    beta_response = await app_client.get("/api/orgs/beta/me")
    assert beta_response.status_code == 200, beta_response.text
    assert beta_response.json()["role"] == "admin"
    assert beta_response.json()["org_id"] == str(beta_id)


async def test_concurrent_sessions_are_independent(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Logging out of one session does not invalidate another.

    The cookie/session strategy is database-backed (``access_tokens``
    table), so two logins from the "same user" create two rows in that
    table. Deleting one row (logout) must not affect the other.

    Two separate clients each log in independently. Client A logs out.
    Client B must still be able to call /me.
    """
    _ = migrated_database
    host, port = container_host_port
    _set_test_env(monkeypatch, host, port)
    await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)

    app = create_app()

    try:
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_a,
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_b,
        ):
            await _login(client_a, USER_A_EMAIL, USER_PASSWORD)
            await _login(client_b, USER_A_EMAIL, USER_PASSWORD)

            # Sanity: both sessions can read /me.
            assert (await client_a.get("/api/auth/me")).status_code == 200
            assert (await client_b.get("/api/auth/me")).status_code == 200

            # Log out of client A.
            logout = await client_a.post("/api/auth/logout")
            assert logout.status_code == 204

            # Client A's session is gone.
            assert (await client_a.get("/api/auth/me")).status_code == 401

            # Client B is unaffected.
            response_b = await client_b.get("/api/auth/me")
            assert response_b.status_code == 200, response_b.text
            assert response_b.json()["email"] == USER_A_EMAIL
    finally:
        await dispose_engine()


async def test_bearer_and_cookie_produce_identical_response(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Same user, same route, two auth modes — bodies are identical.

    Seeds a user and an admin, has the admin mint a token for the user,
    then issues the same request once with the cookie and once with a
    Bearer header (cookies cleared so it's unambiguous). The response
    body must be the same — the auth mechanism is invisible to the
    route handler.
    """
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    await _seed_user(
        migrations_pool,
        SUPERUSER_EMAIL,
        SUPERUSER_PASSWORD,
        is_superuser=True,
    )

    # As superuser, mint a token for User A.
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    token = await _create_token_as_admin(app_client, user_a)
    await app_client.post("/api/auth/logout")
    app_client.cookies.clear()

    # As User A via cookie:
    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    via_cookie = await app_client.get("/api/auth/me")
    assert via_cookie.status_code == 200, via_cookie.text

    # As User A via Bearer:
    app_client.cookies.clear()
    via_bearer = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert via_bearer.status_code == 200, via_bearer.text

    assert via_cookie.json() == via_bearer.json()


async def test_bearer_and_cookie_observe_same_rls(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Bearer and cookie hit the same RLS scoping through the org route.

    Both auth paths land in the same ``current_user_or_token``
    dependency and yield the same ``User``; the org-scoping dependency
    then runs identically. The probe confirms an attacker cannot get a
    different visibility surface by switching auth mode.
    """
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    await _seed_user(
        migrations_pool,
        SUPERUSER_EMAIL,
        SUPERUSER_PASSWORD,
        is_superuser=True,
    )
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id, "member")

    # Mint a token for User A.
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    token = await _create_token_as_admin(app_client, user_a)
    await app_client.post("/api/auth/logout")
    app_client.cookies.clear()

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    via_cookie = await app_client.get("/api/orgs/acme/me")
    assert via_cookie.status_code == 200, via_cookie.text

    app_client.cookies.clear()
    via_bearer = await app_client.get(
        "/api/orgs/acme/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert via_bearer.status_code == 200, via_bearer.text

    assert via_cookie.json() == via_bearer.json()


async def test_revoked_token_rejected_on_org_routes_too(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Revocation propagates to every auth surface, not just /api/auth/me.

    The dual-auth dependency is shared between unscoped and org-scoped
    routes — a regression here (e.g. caching the token lookup at the
    wrong layer) could let revoked tokens still pass on a subset of
    endpoints. The probe asserts uniform rejection.
    """
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    await _seed_user(
        migrations_pool,
        SUPERUSER_EMAIL,
        SUPERUSER_PASSWORD,
        is_superuser=True,
    )
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id, "member")

    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    create = await app_client.post(
        f"/api/admin/users/{user_a}/tokens",
        json={"name": "to-be-revoked"},
    )
    assert create.status_code == 201
    token_body = create.json()
    plaintext = token_body["token"]
    token_id = token_body["id"]

    # Sanity: token works against the org route while still valid.
    app_client.cookies.clear()
    sanity = await app_client.get(
        "/api/orgs/acme/me",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert sanity.status_code == 200, sanity.text

    # Revoke (as superuser via cookie).
    await _login(app_client, SUPERUSER_EMAIL, SUPERUSER_PASSWORD)
    revoke = await app_client.post(
        f"/api/admin/users/{user_a}/tokens/{token_id}/revoke",
    )
    assert revoke.status_code == 204

    # Revoked token must be rejected on the org route as well as /me.
    app_client.cookies.clear()
    on_me = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert on_me.status_code == 401

    on_org = await app_client.get(
        "/api/orgs/acme/me",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert on_org.status_code == 401


async def test_logged_out_cookie_cannot_access_org_routes(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """Logout invalidates the cookie across the entire surface, not just /me.

    The cookie's value is a row in ``access_tokens``; logout deletes
    that row. Any subsequent request bearing the same cookie — to /me
    or to an org-scoped route — must fail at the cookie-validation
    stage. This protects against a regression where the dual-auth
    dependency caches "user from cookie" before the row deletion is
    observed.
    """
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    await _seed_membership(migrations_pool, user_a, acme_id, "member")

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    assert (await app_client.get("/api/auth/me")).status_code == 200
    assert (await app_client.get("/api/orgs/acme/me")).status_code == 200

    logout = await app_client.post("/api/auth/logout")
    assert logout.status_code == 204

    assert (await app_client.get("/api/auth/me")).status_code == 401
    assert (await app_client.get("/api/orgs/acme/me")).status_code == 401


async def test_user_a_constructing_org_b_url_is_403_not_data_leak(
    app_client: AsyncClient,
    migrations_pool: asyncpg.Pool,
) -> None:
    """The classic adversarial probe: type Org B's slug into the URL.

    Authenticated as User A, who is a member of Acme only. Construct
    a URL pointing at Beta. The system must respond with 403 (not 200
    with leaked data, not 401 confusing the caller, not 404 hiding
    Beta's existence to a member of any other org).
    """
    user_a = await _seed_user(migrations_pool, USER_A_EMAIL, USER_PASSWORD)
    user_b = await _seed_user(migrations_pool, USER_B_EMAIL, USER_PASSWORD)
    acme_id = await _seed_org(migrations_pool, "acme", "Acme")
    beta_id = await _seed_org(migrations_pool, "beta", "Beta")
    await _seed_membership(migrations_pool, user_a, acme_id, "member")
    await _seed_membership(migrations_pool, user_b, beta_id, "admin")

    await _login(app_client, USER_A_EMAIL, USER_PASSWORD)
    response = await app_client.get("/api/orgs/beta/me")
    assert response.status_code == 403
    # Sanity: the response body does not leak Beta's name or User B's id.
    assert "beta" not in response.text.lower() or "not a member" in response.text.lower()
    assert str(user_b) not in response.text
