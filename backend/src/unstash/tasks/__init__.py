"""Worker-side task infrastructure.

This package houses Taskiq broker configuration and the worker-side
equivalent of the request-level org-scoping dependency. Tasks live as
top-level decorated coroutines; the broker dispatches them and the
worker runs them with the org context the route handler captured at
queue time.

The route layer queues a task via ``some_task.kiq(...)`` and goes back
to serving requests. The worker pulls the task off Redis, runs it
inside :func:`org_context`, and writes any results back to the
``job_progress`` row referenced in the task payload.
"""

from __future__ import annotations

from unstash.tasks.broker import broker
from unstash.tasks.context import org_context

__all__ = ["broker", "org_context"]
