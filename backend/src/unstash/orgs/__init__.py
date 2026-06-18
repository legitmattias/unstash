"""Org-scoped HTTP routes — the main tenant-facing API surface.

Every route in this module is mounted under ``/api/orgs/{slug}/`` and
depends on ``get_org_scoped_session`` so the request runs inside a
database transaction with ``app.current_org_id`` set. Row-Level
Security policies (ADR 0005) enforce tenant isolation at the database
layer; the dependency makes sure the GUC is set before any query
runs.
"""

from __future__ import annotations

from unstash.orgs.router import orgs_router

__all__ = ["orgs_router"]
