"""Shared test fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from unstash.config import Settings, get_settings
from unstash.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Provide a Settings instance backed by env vars, not Docker secrets."""
    get_settings.cache_clear()

    monkeypatch.setenv("UNSTASH_DATABASE_PASSWORD", "test_password")
    monkeypatch.setenv("UNSTASH_SESSION_SECRET", "test_session_secret")
    monkeypatch.setenv("UNSTASH_ENCRYPTION_KEY", "test_encryption_key")
    monkeypatch.setenv("UNSTASH_ENVIRONMENT", "test")
    monkeypatch.setenv("UNSTASH_LOG_LEVEL", "WARNING")

    return get_settings()


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """Async HTTP client targeting the test application."""
    _ = settings  # Ensure settings fixture runs before app creation
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
