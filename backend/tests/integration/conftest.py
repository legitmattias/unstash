"""Fixtures for tests that need a real Postgres with our custom image.

These tests spin up the ``unstash-postgres:ci`` image via testcontainers, which
runs ``docker/init-db.sh`` at first boot to create the two database roles and
install the four extensions. Tests then connect as either ``unstash_app`` or
``unstash_migrations`` depending on what they're verifying.

Requires Docker to be available on the host and the ``unstash-postgres:ci``
image to be built locally. The CI integration job builds the image as part of
its setup; for local runs, build with::

    docker build -f docker/postgres.Dockerfile -t unstash-postgres:ci .
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

from unstash.config import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


# Generated once per test process; no literal for secret scanners to match.
TEST_APP_PASSWORD = secrets.token_urlsafe(16)
TEST_MIGRATIONS_PASSWORD = secrets.token_urlsafe(16)
TEST_ADMIN_PASSWORD = secrets.token_urlsafe(16)
TEST_BOOTSTRAP_PASSWORD = secrets.token_urlsafe(16)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Spin up a fresh postgres container with init-db.sh roles + extensions.

    Session-scoped: the container is created once per test session. Tests use
    unique tenant identifiers to avoid collisions; RLS keeps tenants isolated
    even on shared schema.
    """
    container = (
        PostgresContainer(
            image="unstash-postgres:ci",
            username="postgres",
            password=TEST_BOOTSTRAP_PASSWORD,
            dbname="unstash",
        )
        .with_env("UNSTASH_APP_DB_PASSWORD", TEST_APP_PASSWORD)
        .with_env("UNSTASH_MIGRATIONS_DB_PASSWORD", TEST_MIGRATIONS_PASSWORD)
        .with_env("UNSTASH_ADMIN_DB_PASSWORD", TEST_ADMIN_PASSWORD)
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def container_host_port(postgres_container: PostgresContainer) -> tuple[str, int]:
    """Resolved (host, port) for the postgres container."""
    host = postgres_container.get_container_host_ip()
    port = int(postgres_container.get_exposed_port(5432))
    return host, port


def _set_env_to_container(
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    port: int,
) -> None:
    """Point Pydantic Settings at the test container via env vars."""
    monkeypatch.setenv("UNSTASH_DATABASE_HOST", host)
    monkeypatch.setenv("UNSTASH_DATABASE_PORT", str(port))
    monkeypatch.setenv("UNSTASH_DATABASE_NAME", "unstash")
    monkeypatch.setenv("UNSTASH_DATABASE_USER", "unstash_app")
    monkeypatch.setenv("UNSTASH_DATABASE_MIGRATIONS_USER", "unstash_migrations")
    monkeypatch.setenv("database_password", TEST_APP_PASSWORD)
    monkeypatch.setenv("database_migrations_password", TEST_MIGRATIONS_PASSWORD)
    monkeypatch.setenv("database_admin_password", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("session_secret", "test_session_secret")
    monkeypatch.setenv("encryption_key", "test_encryption_key")
    get_settings.cache_clear()


@pytest.fixture
def settings_for_container(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
):
    """Configure Pydantic Settings so alembic env.py can build the migrations URL."""
    _set_env_to_container(monkeypatch, *container_host_port)
    return get_settings()


def _backend_root() -> Path:
    """Path to the backend/ directory (where alembic.ini lives)."""
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def backend_root() -> Path:
    """Path to the backend/ directory, exposed for tests that need it."""
    return _backend_root()


def _alembic_config() -> Config:
    """Alembic Config bound to backend/alembic.ini."""
    return Config(str(_backend_root() / "alembic.ini"))


@pytest.fixture
def migrated_database(settings_for_container) -> None:
    """Apply alembic upgrade head against the test container.

    Function-scoped on purpose: each test starts at head with a clean schema.
    Tests that need a different revision drive their own alembic.command calls
    after this fixture has set up the environment.
    """
    _ = settings_for_container
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def app_pool(
    container_host_port: tuple[str, int],
    migrated_database: None,
) -> AsyncIterator[asyncpg.Pool]:
    """asyncpg pool connecting as the unstash_app role."""
    _ = migrated_database
    host, port = container_host_port
    pool = await asyncpg.create_pool(
        host=host,
        port=port,
        user="unstash_app",
        password=TEST_APP_PASSWORD,
        database="unstash",
        min_size=1,
        max_size=5,
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def migrations_pool(
    container_host_port: tuple[str, int],
    migrated_database: None,
) -> AsyncIterator[asyncpg.Pool]:
    """asyncpg pool connecting as the unstash_migrations role (BYPASSRLS)."""
    _ = migrated_database
    host, port = container_host_port
    pool = await asyncpg.create_pool(
        host=host,
        port=port,
        user="unstash_migrations",
        password=TEST_MIGRATIONS_PASSWORD,
        database="unstash",
        min_size=1,
        max_size=5,
    )
    try:
        yield pool
    finally:
        await pool.close()
