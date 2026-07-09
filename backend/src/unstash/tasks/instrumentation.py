"""Lightweight worker instrumentation emitted as structured log fields.

The worker runs a single process with a hard container memory limit,
so ingestion throughput and memory headroom need to be visible before
they become incidents. Until a metrics stack is deployed, structured
log fields are the transport: greppable on the host today, ingestible
by a log pipeline later without code changes.

Instrumentation must never break ingestion — every helper degrades to
``None`` and a warning on failure.
"""

from __future__ import annotations

import resource
from typing import TYPE_CHECKING, cast

import structlog
from redis.asyncio import Redis
from taskiq_redis import ListQueueBroker

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from taskiq import AsyncBroker

logger = structlog.get_logger(__name__)


def peak_rss_mib() -> float:
    """Lifetime peak resident set size of this process, in MiB.

    ``ru_maxrss`` is a high-water mark, not a current reading: it never
    decreases, so after a large parse it reports that parse's peak
    footprint even once memory is released. That is the number to
    compare against the worker container's memory limit.
    """
    # ru_maxrss is expressed in KiB on Linux.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def queue_depth(broker: AsyncBroker) -> int | None:
    """Number of tasks currently waiting in the broker queue.

    Only meaningful for the Redis list broker; returns ``None`` for the
    in-memory test broker or when Redis cannot be reached.
    """
    if not isinstance(broker, ListQueueBroker):
        return None
    try:
        redis: Redis = Redis(connection_pool=broker.connection_pool)
        try:
            # redis-py's async stubs type llen as returning int; at
            # runtime the async client returns a coroutine.
            return int(await cast("Awaitable[int]", redis.llen(broker.queue_name)))
        finally:
            await redis.aclose()
    except Exception as exc:
        logger.warning(
            "queue_depth_unavailable",
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )
        return None
