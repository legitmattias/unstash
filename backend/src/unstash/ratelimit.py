"""Redis-backed fixed-window rate limiting.

A minimal counter used to throttle login attempts and the endpoints that
trigger paid or expensive work. Fixed-window rather than a token bucket:
at pilot scale the coarser window is enough.

The store is the Redis already used for the task queue, so no new
dependency or service. Callers treat a Redis failure as fail-open (a
Redis outage must not lock users out).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import structlog
from fastapi import HTTPException, status

if TYPE_CHECKING:
    from fastapi import Request

logger = structlog.get_logger(__name__)

# INCR and the first-hit EXPIRE run as one script. Issued as two commands, a
# connection lost between them leaves a counter with no expiry, and the key
# then blocks its client permanently.
_HIT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class RateLimitStore(Protocol):
    """The one Redis operation the fixed-window limiter needs."""

    async def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> int:
        """Run ``script`` atomically and return its integer result."""
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
    count = await store.eval(_HIT_SCRIPT, 1, key, window_seconds)
    return int(count) <= limit


async def enforce_rate_limit(
    request: Request,
    key: str,
    *,
    limit: int,
    window_seconds: int,
    detail: str,
) -> None:
    """Raise 429 when ``key`` has exceeded ``limit`` hits in the window.

    No-op when no Redis client is attached to the application (ASGI test
    transports do not run the lifespan). A Redis error fails open.
    """
    store = getattr(request.app.state, "redis", None)
    if store is None:
        return
    try:
        allowed = await within_fixed_window(
            store,
            f"ratelimit:{key}",
            limit=limit,
            window_seconds=window_seconds,
        )
    except Exception as exc:
        logger.warning("rate_limit_unavailable", key=key, error=str(exc))
        return
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)


def client_ip(request: Request) -> str:
    """Client IP as recorded by the reverse proxy.

    Behind the reverse proxy ``request.client.host`` is the proxy. The proxy
    appends the address it accepted the connection from to
    ``X-Forwarded-For``, so the last entry is the one the client cannot
    choose; earlier entries are whatever the client sent. This assumes
    exactly one trusted proxy in front of the application.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else "unknown"
