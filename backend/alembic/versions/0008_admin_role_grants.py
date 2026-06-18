"""Grant DML on existing tables to the unstash_admin role.

The ``unstash_admin`` role is created by ``docker/init-db.sh`` on first
boot and is used exclusively by the superuser-gated cross-tenant admin
routes — see ADR 0006. ``init-db.sh`` already sets
``ALTER DEFAULT PRIVILEGES`` so any future table created by
``unstash_migrations`` automatically grants DML to ``unstash_admin``.

This migration covers the **existing-database upgrade path**: for any
database that was initialised before the admin role existed, the
default-privileges hook never fired for the tables already in place.
Granting explicitly here brings those tables to parity.

The migration is safe on fresh databases too — the GRANTs are idempotent
and resolve to no-ops when the privileges are already present.

Important: this migration assumes the ``unstash_admin`` role already
exists. ``init-db.sh`` creates it on fresh databases. For an existing
production database, a privileged operator must run
``CREATE ROLE unstash_admin ...`` once before the migration is applied;
the migration cannot create roles because ``unstash_migrations`` has
``NOCREATEROLE``. The operator runbook in the PR description covers
this.

Revision ID: 0008_admin_role_grants
Revises: 0007_access_tokens
Create Date: 2026-06-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_admin_role_grants"
down_revision: str | None = "0007_access_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Grant DML on every existing table and SELECT/USAGE on sequences."""
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        "TO unstash_admin;"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO unstash_admin;"
    )
    # Belt-and-suspenders default privileges: init-db.sh already sets these
    # on fresh databases, but re-issuing here is idempotent and protects the
    # case where init-db.sh was not yet updated when the database was first
    # provisioned.
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO unstash_admin;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO unstash_admin;"
    )


def downgrade() -> None:
    """Revoke the privileges granted in ``upgrade``."""
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM unstash_admin;"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE unstash_migrations IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM unstash_admin;"
    )
    op.execute(
        "REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM unstash_admin;"
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        "FROM unstash_admin;"
    )
