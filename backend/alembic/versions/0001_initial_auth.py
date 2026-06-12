"""Initial auth schema: organisations, users, org_memberships.

Revision ID: 0001_initial_auth
Revises:
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Alembic identifiers.
revision: str = "0001_initial_auth"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the citext extension and the three auth tables."""
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "organisations",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column(
            "locale",
            sa.String(),
            server_default=sa.text("'sv-SE'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organisations")),
        sa.UniqueConstraint("slug", name=op.f("uq_organisations_slug")),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]*[a-z0-9]$'",
            name=op.f("ck_organisations_slug_format"),
        ),
    )

    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_superuser",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_table(
        "org_memberships",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_memberships")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_org_memberships_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_org_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "org_id",
            "user_id",
            name=op.f("uq_org_memberships_org_id"),
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')",
            name=op.f("ck_org_memberships_role_valid"),
        ),
    )
    op.create_index(
        op.f("ix_org_memberships_org_id"),
        "org_memberships",
        ["org_id"],
    )
    op.create_index(
        op.f("ix_org_memberships_user_id"),
        "org_memberships",
        ["user_id"],
    )


def downgrade() -> None:
    """Drop the three auth tables.

    The citext extension is intentionally left in place — extensions are
    cluster-global and may be relied on by other objects outside the
    Alembic-managed schema.
    """
    op.drop_index(op.f("ix_org_memberships_user_id"), table_name="org_memberships")
    op.drop_index(op.f("ix_org_memberships_org_id"), table_name="org_memberships")
    op.drop_table("org_memberships")
    op.drop_table("users")
    op.drop_table("organisations")
