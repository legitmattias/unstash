"""Taskiq broker configuration.

In production and staging the broker is a Redis-backed FIFO queue
(``ListQueueBroker``) with results persisted to the same Redis. In
tests we swap in an :class:`InMemoryBroker` so jobs run in-process and
no Redis container is required for unit-level coverage.

The broker is constructed lazily so test code can call
:func:`use_in_memory_broker` from a fixture before any task is
imported, swapping the module-level ``broker`` reference before the
``@broker.task`` decorators run on import.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from taskiq import InMemoryBroker
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from unstash.config import get_settings

if TYPE_CHECKING:
    from taskiq import AsyncBroker


def _build_broker() -> AsyncBroker:
    """Return the broker appropriate for the current environment.

    Tests can opt into the in-memory broker by setting the env var
    ``UNSTASH_TASKIQ_IN_MEMORY=1`` before this module is imported.
    """
    if os.environ.get("UNSTASH_TASKIQ_IN_MEMORY") == "1":
        return InMemoryBroker()
    settings = get_settings()
    return ListQueueBroker(url=settings.redis_url).with_result_backend(
        RedisAsyncResultBackend(redis_url=settings.redis_url),
    )


broker: AsyncBroker = _build_broker()
