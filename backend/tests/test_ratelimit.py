"""Unit tests for the fixed-window rate limiter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from unstash.ratelimit import client_ip, enforce_rate_limit, within_fixed_window


class _FakeRedis:
    """In-memory stand-in executing the limiter's INCR-then-EXPIRE script."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self.expire_calls = 0

    async def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> int:
        assert numkeys == 1
        key, window = str(keys_and_args[0]), int(keys_and_args[1])
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.counts[key] == 1:
            self.expiries[key] = window
            self.expire_calls += 1
        return self.counts[key]


class _BrokenRedis:
    async def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> int:
        raise ConnectionError("store unavailable")


def _request(*, redis: Any = None, headers: dict[str, str] | None = None) -> Any:
    state = SimpleNamespace()
    if redis is not None:
        state.redis = redis
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        headers=headers or {},
        client=SimpleNamespace(host="10.0.0.9"),
    )


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
    for _ in range(4):
        await within_fixed_window(store, "k", limit=10, window_seconds=45)
    assert store.expiries == {"k": 45}
    assert store.expire_calls == 1


async def test_keys_are_independent() -> None:
    store = _FakeRedis()
    assert await within_fixed_window(store, "a", limit=1, window_seconds=60) is True
    assert await within_fixed_window(store, "a", limit=1, window_seconds=60) is False
    assert await within_fixed_window(store, "b", limit=1, window_seconds=60) is True


async def test_enforce_raises_429_past_the_limit() -> None:
    request = _request(redis=_FakeRedis())
    await enforce_rate_limit(request, "search:u1", limit=1, window_seconds=60, detail="slow down")

    with pytest.raises(HTTPException) as exc_info:
        await enforce_rate_limit(
            request, "search:u1", limit=1, window_seconds=60, detail="slow down"
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "slow down"


async def test_enforce_is_a_no_op_without_a_store() -> None:
    request = _request()
    for _ in range(3):
        await enforce_rate_limit(request, "k", limit=1, window_seconds=60, detail="x")


async def test_enforce_fails_open_when_the_store_errors() -> None:
    request = _request(redis=_BrokenRedis())
    await enforce_rate_limit(request, "k", limit=1, window_seconds=60, detail="x")


def test_client_ip_uses_the_proxy_appended_hop() -> None:
    request = _request(headers={"x-forwarded-for": "203.0.113.7, 198.51.100.4"})
    assert client_ip(request) == "198.51.100.4"


def test_client_ip_ignores_a_client_supplied_prefix() -> None:
    spoofed = _request(headers={"x-forwarded-for": "1.1.1.1,2.2.2.2, 198.51.100.4"})
    honest = _request(headers={"x-forwarded-for": "198.51.100.4"})
    assert client_ip(spoofed) == client_ip(honest)


def test_client_ip_falls_back_to_the_peer_address() -> None:
    assert client_ip(_request()) == "10.0.0.9"
