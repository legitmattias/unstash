"""Application settings with Docker Compose file-based secrets support.

Settings are loaded in this priority order (highest wins):

1. Constructor arguments
2. Environment variables (prefixed ``UNSTASH_``)
3. Docker Compose secrets mounted at ``/run/secrets/``
4. Field defaults

Secret fields use explicit ``alias=<field_name>`` to opt out of the
``UNSTASH_`` env prefix mechanism. This means secret files are read from
``/run/secrets/<field_name>`` (no prefix), keeping the file naming convention
clean. In tests, pass values via constructor (``Settings(database_password=...)``)
or point ``UNSTASH_SECRETS_DIR`` at a directory containing the expected files.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# Docker Compose mounts secrets here by default. Override via env var for tests
# or non-Docker development.
_SECRETS_DIR = os.environ.get("UNSTASH_SECRETS_DIR", "/run/secrets")


class Settings(BaseSettings):
    """Application configuration.

    Non-sensitive settings are loaded from ``UNSTASH_``-prefixed environment
    variables.  Sensitive settings (passwords, API keys) are loaded from
    Docker Compose secret files in ``/run/secrets/``.
    """

    model_config = SettingsConfigDict(  # type: ignore[call-arg]  # secrets_dir_missing not in type stubs yet
        env_prefix="UNSTASH_",
        secrets_dir=_SECRETS_DIR if Path(_SECRETS_DIR).is_dir() else None,
        secrets_dir_missing="ok",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Environment -----------------------------------------------------------

    environment: str = Field(
        default="development",
        description="Runtime environment: development, staging, production.",
    )
    debug: bool = Field(default=False, description="Enable FastAPI debug mode.")
    log_level: str = Field(default="INFO", description="Logging level.")

    # -- Database (non-secret fields) ------------------------------------------

    database_host: str = Field(default="postgres")
    database_port: int = Field(default=5432)
    database_name: str = Field(default="unstash")
    database_user: str = Field(default="unstash_app")
    database_migrations_user: str = Field(default="unstash_migrations")
    database_pool_size: int = Field(default=5, ge=1)
    database_pool_max_overflow: int = Field(default=5, ge=0)
    database_pool_timeout: int = Field(
        default=30,
        ge=1,
        description="Seconds to wait for an available connection before raising.",
    )

    # -- Secrets ---------------------------------------------------------------
    # Loaded from /run/secrets/<field_name> via Pydantic Settings' secrets_dir
    # source. Each field uses an explicit alias matching the secret file name —
    # this opts the field out of the env_prefix mechanism, so the file name
    # stays as plain ``database_password`` rather than ``UNSTASH_database_password``.
    # In tests, pass values via constructor or set UNSTASH_SECRETS_DIR to a
    # directory with the expected file names.

    database_password: str = Field(default="", alias="database_password")
    database_migrations_password: str = Field(
        default="",
        alias="database_migrations_password",
    )
    session_secret: str = Field(default="", alias="session_secret")
    encryption_key: str = Field(default="", alias="encryption_key")

    # -- Redis -----------------------------------------------------------------

    redis_host: str = Field(default="redis")
    redis_port: int = Field(default=6379)

    # -- External APIs ---------------------------------------------------------
    # Added when their respective features are implemented.

    jina_api_key: str = Field(default="", alias="jina_api_key")
    mistral_api_key: str = Field(default="", alias="mistral_api_key")

    # -- Derived properties ----------------------------------------------------

    @property
    def database_url(self) -> URL:
        """Async database URL for the application role (SQLAlchemy + asyncpg)."""
        return self._build_db_url(
            self.database_user,
            self.database_password,
            "postgresql+asyncpg",
        )

    @property
    def database_migrations_url(self) -> URL:
        """Sync database URL for the migrations role (used by Alembic)."""
        return self._build_db_url(
            self.database_migrations_user,
            self.database_migrations_password,
            "postgresql+psycopg",
        )

    def _build_db_url(self, user: str, password: str, driver: str) -> URL:
        return URL.create(
            drivername=driver,
            username=user,
            password=password,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )

    @property
    def redis_url(self) -> str:
        """Redis connection URL."""
        return f"redis://{self.redis_host}:{self.redis_port}/0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton.

    In tests, clear the cache and set env vars before calling::

        get_settings.cache_clear()
        monkeypatch.setenv("UNSTASH_DATABASE_PASSWORD", "test")
        settings = get_settings()
    """
    return Settings()
