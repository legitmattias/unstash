"""Fresh Postgres for the eval runner: testcontainer at migration head.

Mirrors the integration-test fixtures (same custom image, same env plumbing
for alembic) but as a plain async context manager usable from a script.
Yields an asyncpg pool connected as the migrations role (BYPASSRLS), so the
runner needs no tenant-GUC dance.

Requires the ``unstash-postgres:ci`` image::

    docker build -f docker/postgres.Dockerfile -t unstash-postgres:ci .
"""

from __future__ import annotations

import contextlib
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer

BACKEND = Path(__file__).resolve().parents[2]


@contextlib.asynccontextmanager
async def fresh_database() -> AsyncIterator[asyncpg.Pool]:
    """Start postgres, migrate to head, yield a migrations-role pool."""
    pw_app = secrets.token_urlsafe(16)
    pw_mig = secrets.token_urlsafe(16)
    pw_admin = secrets.token_urlsafe(16)
    pw_boot = secrets.token_urlsafe(16)

    container = (
        PostgresContainer(
            image="unstash-postgres:ci",
            username="postgres",
            password=pw_boot,
            dbname="unstash",
        )
        .with_env("UNSTASH_APP_DB_PASSWORD", pw_app)
        .with_env("UNSTASH_MIGRATIONS_DB_PASSWORD", pw_mig)
        .with_env("UNSTASH_ADMIN_DB_PASSWORD", pw_admin)
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(5432))

        # alembic env.py builds its URL from application Settings.
        os.environ.update(
            {
                "UNSTASH_DATABASE_HOST": host,
                "UNSTASH_DATABASE_PORT": str(port),
                "UNSTASH_DATABASE_NAME": "unstash",
                "UNSTASH_DATABASE_USER": "unstash_app",
                "UNSTASH_DATABASE_MIGRATIONS_USER": "unstash_migrations",
                "database_password": pw_app,
                "database_migrations_password": pw_mig,
                "database_admin_password": pw_admin,
                "session_secret": secrets.token_urlsafe(16),
                "encryption_key": secrets.token_urlsafe(16),
            }
        )
        from unstash.config import get_settings

        get_settings.cache_clear()
        cwd = Path.cwd()
        os.chdir(BACKEND)
        try:
            command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
        finally:
            os.chdir(cwd)

        pool = await asyncpg.create_pool(
            host=host,
            port=port,
            user="unstash_migrations",
            password=pw_mig,
            database="unstash",
            min_size=1,
            max_size=5,
        )
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        container.stop()
