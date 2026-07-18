"""Redis-backed fixed-window rate limiting.

A minimal counter used to throttle the login endpoint. Fixed-window
rather than a token bucket: at pilot scale the coarser window is enough,
and the two-command INCR/EXPIRE keeps it cheap and easy to reason about.

The store is the Redis already used for the task queue, so no new
dependency or service. Callers treat a Redis failure as fail-open (a
Redis outage must not lock users out of login).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastapi import Request


class RateLimitStore(Protocol):
    """The two Redis operations the fixed-window limiter needs."""

    async def incr(self, name: str) -> int:
        """Increment ``name`` and return the new value."""
        ...

    async def expire(self, name: str, time: int) -> bool:
        """Set ``name`` to expire after ``time`` seconds."""
        ...


async def within_fixed_window(
    store: RateLimitStore,
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> bool:
    """Record a hit and report whether it is within ``limit`` per window.

    The first hit in a window sets the key's expiry, so the window slides
    forward only when traffic stops for ``window_seconds``.
    """
    count = await store.incr(key)
    if count == 1:
        await store.expire(key, window_seconds)
    return count <= limit


def client_ip(request: Request) -> str:
    """Best-effort client IP, trusting the reverse proxy's forwarded header.

    Behind the reverse proxy ``request.client.host`` is the proxy, so the
    first ``X-Forwarded-For`` hop is the real client. This trusts the
    proxy to set that header; it is not a security boundary on its own.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
