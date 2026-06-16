"""Row-Level Security policies for tenant isolation.

Enables Row-Level Security on every tenant-scoped table and attaches a
single uniform policy that constrains rows to the current org_id taken
from the ``app.current_org_id`` configuration variable.

The policy is the same shape on every table — the predicate ``org_id =
current_setting('app.current_org_id')::uuid`` matches the composite
indexes already in place. Reading an unset GUC raises an error rather
than returning null, so a request that forgets to set the variable
fails loudly rather than silently returning empty results.

Two tables are intentionally NOT covered:

  - ``organisations`` — global. The whole point of looking it up is to
    discover which org the request belongs to before ``app.current_org_id``
    is set. Application-layer authorisation handles read-access to this
    table.
  - ``users`` — global identity that spans orgs. RLS on users would need
    a more complex predicate ("user is a member of an org I belong to")
    that introduces cross-table policy logic. Application-layer
    authorisation handles user lookups.

The policy is granted only to ``unstash_app``. ``unstash_migrations``
has BYPASSRLS and is unaffected.

Revision ID: 0006_rls_policies
Revises: 0005_jobs_and_audit
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0006_rls_policies"
down_revision: str | None = "0005_jobs_and_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that carry an ``org_id`` column and get the uniform tenant_isolation
# policy. Order is irrelevant for correctness but is grouped roughly by
# topological dependency for readability.
TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "org_memberships",
    "documents",
    "chunks",
    "connectors",
    "search_logs",
    "job_progress",
    "audit_log",
)

# The uniform predicate. Both USING and WITH CHECK use the same expression
# so that rows visible to the role are exactly the rows it can write.
_POLICY_PREDICATE = "org_id = current_setting('app.current_org_id')::uuid"
_POLICY_NAME = "tenant_isolation"


def upgrade() -> None:
    """Enable RLS and attach the tenant_isolation policy on every tenant-scoped table."""
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {_POLICY_NAME} ON {table}
                FOR ALL
                TO unstash_app
                USING ({_POLICY_PREDICATE})
                WITH CHECK ({_POLICY_PREDICATE});
            """
        )


def downgrade() -> None:
    """Drop the policy and disable RLS on every tenant-scoped table."""
    # Reverse order is not required for correctness — policies are
    # independent per table — but follows the convention.
    for table in reversed(TENANT_SCOPED_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
