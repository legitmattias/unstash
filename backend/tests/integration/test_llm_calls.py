"""LLM call recorder against real Postgres.

Verifies that record_llm_call writes an org-scoped row visible under the
caller's org context and emits a log event carrying the OTel GenAI
attribute names (ADR 0011). RLS coverage for the table itself lives in
test_rls.py's table-driven checks.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from structlog.testing import capture_logs

from tests.integration.conftest import (
    TEST_APP_PASSWORD,
    TEST_MIGRATIONS_PASSWORD,
)
from unstash.config import get_settings
from unstash.db.engine import dispose_engine, get_engine
from unstash.db.models import LlmCall, LlmCallOutcome
from unstash.db.session import get_sessionmaker
from unstash.llm import record_llm_call
from unstash.tasks import org_context

if TYPE_CHECKING:
    import asyncpg


@pytest.fixture
def _engine_env(
    container_host_port: tuple[str, int],
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: None,
) -> None:
    """Point the app engine at the testcontainer database."""
    _ = migrated_database
    host, port = container_host_port
    monkeypatch.setenv("UNSTASH_DATABASE_HOST", host)
    monkeypatch.setenv("UNSTASH_DATABASE_PORT", str(port))
    monkeypatch.setenv("UNSTASH_DATABASE_NAME", "unstash")
    monkeypatch.setenv("UNSTASH_DATABASE_USER", "unstash_app")
    monkeypatch.setenv("database_password", TEST_APP_PASSWORD)
    monkeypatch.setenv("database_migrations_password", TEST_MIGRATIONS_PASSWORD)
    monkeypatch.setenv("session_secret", uuid.uuid4().hex)
    monkeypatch.setenv("encryption_key", uuid.uuid4().hex)
    monkeypatch.setenv("UNSTASH_ENVIRONMENT", "test")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


async def _seed_org(pool: asyncpg.Pool) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO organisations (name, slug) VALUES ($1, $2) RETURNING id",
            f"Llm Org {suffix}",
            f"llm-{suffix}",
        )


@pytest.mark.usefixtures("_engine_env")
async def test_record_llm_call_writes_row_and_otel_event(
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id = await _seed_org(migrations_pool)
    try:
        with capture_logs() as logs:
            async with org_context(org_id) as session:
                await record_llm_call(
                    session,
                    org_id=org_id,
                    purpose="cluster_label",
                    model="test-model",
                    latency_ms=420,
                    outcome=LlmCallOutcome.OK,
                    prompt_tokens=250,
                    completion_tokens=12,
                    cost=0.000375,
                )

        async with org_context(org_id) as session:
            row = (
                await session.execute(select(LlmCall).where(LlmCall.org_id == org_id))
            ).scalar_one()
            assert row.purpose == "cluster_label"
            assert row.model == "test-model"
            assert row.prompt_tokens == 250
            assert row.completion_tokens == 12
            assert float(row.cost) == pytest.approx(0.000375)
            assert row.latency_ms == 420
            assert row.outcome == "ok"
            assert row.error is None

        events = [e for e in logs if e.get("event") == "llm_call"]
        assert len(events) == 1
        event = events[0]
        assert event["gen_ai.operation.name"] == "cluster_label"
        assert event["gen_ai.request.model"] == "test-model"
        assert event["gen_ai.usage.input_tokens"] == 250
        assert event["gen_ai.usage.output_tokens"] == 12
        assert event["outcome"] == "ok"
    finally:
        await dispose_engine()


@pytest.mark.usefixtures("_engine_env")
async def test_record_llm_call_error_outcome(
    migrations_pool: asyncpg.Pool,
) -> None:
    org_id = await _seed_org(migrations_pool)
    try:
        async with org_context(org_id) as session:
            await record_llm_call(
                session,
                org_id=org_id,
                purpose="cluster_label",
                model="test-model",
                latency_ms=1000,
                outcome=LlmCallOutcome.ERROR,
                error="provider timeout",
            )

        async with org_context(org_id) as session:
            row = (
                await session.execute(select(LlmCall).where(LlmCall.org_id == org_id))
            ).scalar_one()
            assert row.outcome == "error"
            assert row.error == "provider timeout"
            assert row.prompt_tokens is None
            assert row.cost is None
    finally:
        await dispose_engine()
