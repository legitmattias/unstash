"""User-scoped read policy on org_memberships.

Adds a second, permissive ``FOR SELECT`` policy so a user can read their
own membership rows across every org they belong to — the data behind
``GET /api/me/organisations`` and the org picker. The existing
``tenant_isolation`` policy answers "rows in the current org"; this one
answers "my rows, in any org". Permissive policies are OR-combined, so a
row is selectable when either predicate holds.

The predicate uses ``current_setting('app.current_user_id', true)`` with
the missing-ok flag: org-scoped requests never set that GUC, so there the
predicate resolves to NULL (no rows) and only ``tenant_isolation`` applies.
User-scoped requests set ``app.current_user_id`` and also set
``app.current_org_id`` to the nil UUID so ``tenant_isolation`` (which reads
that GUC without missing-ok) evaluates to a valid false rather than raising.

Only ``org_memberships`` is affected, and only reads. Writes remain under
``tenant_isolation``.

Revision ID: 0016_own_membership_policy
Revises: 0015_search_log_feedback
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_own_membership_policy"
down_revision: str | None = "0015_search_log_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_NAME = "own_memberships_read"
_PREDICATE = "user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def upgrade() -> None:
    """Attach the own_memberships_read policy to org_memberships."""
    op.execute(
        f"""
        CREATE POLICY {_POLICY_NAME} ON org_memberships
            FOR SELECT
            TO unstash_app
            USING ({_PREDICATE});
        """,
    )


def downgrade() -> None:
    """Drop the own_memberships_read policy."""
    op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON org_memberships;")
