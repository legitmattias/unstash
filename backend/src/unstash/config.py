"""Application settings with Docker Compose file-based secrets support.

Settings are loaded in this priority order (highest wins):

1. Constructor arguments
2. Environment variables (prefixed ``UNSTASH_``)
3. Docker Compose secrets mounted at ``/run/secrets/``
4. Field defaults

For secrets, Pydantic Settings reads ``/run/secrets/<field_name>`` as the value.
For example, field ``database_password`` is read from ``/run/secrets/database_password``.
In tests, set ``UNSTASH_DATABASE_PASSWORD`` as an env var instead — no secrets dir needed.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # -- Secrets ---------------------------------------------------------------
    # Loaded from /run/secrets/<field_name> in Docker, or UNSTASH_<FIELD_NAME>
    # env var in tests.  Defaults are provided ONLY so the app can start in
    # development without a full secrets setup — they must be overridden in
    # staging and production.

    database_password: str = Field(default="")
    session_secret: str = Field(default="")
    encryption_key: str = Field(default="")

    # -- Redis -----------------------------------------------------------------

    redis_host: str = Field(default="redis")
    redis_port: int = Field(default=6379)

    # -- External APIs ---------------------------------------------------------
    # Added when their respective features are implemented.

    jina_api_key: str = Field(default="")
    mistral_api_key: str = Field(default="")

    # -- Derived properties ----------------------------------------------------

    @property
    def database_url(self) -> str:
        """Async database URL for SQLAlchemy + asyncpg."""
        return (
            f"postgresql+asyncpg://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
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
