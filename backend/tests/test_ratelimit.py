"""Unit tests for the fixed-window rate-limit helper."""

from __future__ import annotations

import pytest

from unstash.ratelimit import within_fixed_window


class _FakeRedis:
    """Minimal in-memory INCR/EXPIRE store."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    async def incr(self, name: str) -> int:
        self.counts[name] = self.counts.get(name, 0) + 1
        return self.counts[name]

    async def expire(self, name: str, time: int) -> bool:
        self.expiries[name] = time
        return True


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_allows_up_to_the_limit_then_blocks() -> None:
    store = _FakeRedis()
    verdicts = [await within_fixed_window(store, "k", limit=3, window_seconds=60) for _ in range(5)]
    assert verdicts == [True, True, True, False, False]


async def test_expiry_set_once_on_first_hit() -> None:
    store = _FakeRedis()
    for _ in range(3):
        await within_fixed_window(store, "k", limit=10, window_seconds=45)
    assert store.expiries == {"k": 45}


async def test_keys_are_independent() -> None:
    store = _FakeRedis()
    assert await within_fixed_window(store, "a", limit=1, window_seconds=60) is True
    assert await within_fixed_window(store, "a", limit=1, window_seconds=60) is False
    assert await within_fixed_window(store, "b", limit=1, window_seconds=60) is True
