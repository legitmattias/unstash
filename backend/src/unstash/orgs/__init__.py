"""Org-scoped HTTP routes — the main tenant-facing API surface.

Every route mounted under ``/api/orgs/{slug}/`` depends on
:class:`OrgContext` (yielded by :func:`get_org_context`), which opens
a per-request transaction with ``app.current_org_id`` set. Row-Level
Security policies (ADR 0005) enforce tenant isolation at the database
layer; the dependency guarantees the GUC is set before any query runs.
"""

from __future__ import annotations

from unstash.orgs.dependencies import (
    CurrentUserDep,
    OrgContext,
    OrgContextDep,
    get_org_context,
)
from unstash.orgs.router import orgs_router

__all__ = [
    "CurrentUserDep",
    "OrgContext",
    "OrgContextDep",
    "get_org_context",
    "orgs_router",
]
