"""Admin (superuser-only) HTTP routes.

These endpoints manage users and their org memberships from the
operator's side. They are not part of the public/tenant API surface;
their entire point is that ``users.is_superuser`` must be true. The
gate is enforced by ``current_superuser`` on every route handler.

Self-signup is intentionally not provided in this milestone — accounts
are operator-created.
"""

from __future__ import annotations

from unstash.admin.router import admin_router

__all__ = ["admin_router"]
