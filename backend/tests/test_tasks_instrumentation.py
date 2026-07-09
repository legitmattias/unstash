"""Tests for the worker instrumentation helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from taskiq import InMemoryBroker
from taskiq_redis import ListQueueBroker

from unstash.tasks import instrumentation
from unstash.tasks.instrumentation import peak_rss_mib, queue_depth

if TYPE_CHECKING:
    import pytest


def test_peak_rss_is_positive_and_plausible() -> None:
    rss = peak_rss_mib()
    # A running Python process needs at least a few MiB and this test
    # process should stay well under the worker's container limit.
    assert 1 < rss < 16_384


async def test_queue_depth_is_none_for_in_memory_broker() -> None:
    assert await queue_depth(InMemoryBroker()) is None


def _list_queue_broker() -> ListQueueBroker:
    # Real broker object (constructing does not connect); the Redis
    # client is patched in each test so nothing reaches the network.
    return ListQueueBroker(url="redis://localhost:6379", queue_name="taskiq")


async def test_queue_depth_reads_llen_for_redis_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = MagicMock()
    fake_redis.llen = AsyncMock(return_value=7)
    fake_redis.aclose = AsyncMock()
    monkeypatch.setattr(instrumentation, "Redis", MagicMock(return_value=fake_redis))

    assert await queue_depth(_list_queue_broker()) == 7
    fake_redis.llen.assert_awaited_once_with("taskiq")
    fake_redis.aclose.assert_awaited_once()


async def test_queue_depth_degrades_to_none_on_redis_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = MagicMock()
    fake_redis.llen = AsyncMock(side_effect=ConnectionError("redis down"))
    fake_redis.aclose = AsyncMock()
    monkeypatch.setattr(instrumentation, "Redis", MagicMock(return_value=fake_redis))

    assert await queue_depth(_list_queue_broker()) is None
