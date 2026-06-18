"""Server-side session tokens table for FastAPI-Users cookie auth.

Adds the ``access_tokens`` table used by FastAPI-Users' database session
strategy. Each row is a live session — the cookie value is the ``token``
column. The application looks the row up at every request, which is what
makes server-side logout possible.

Not org-scoped: sessions identify a user, the org context is set per
request by middleware from the URL slug. No RLS for the same reason
``users`` has no RLS.

Revision ID: 0007_access_tokens
Revises: 0006_rls_policies
Create Date: 2026-06-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_access_tokens"
down_revision: str | None = "0006_rls_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the access_tokens table."""
    op.create_table(
        "access_tokens",
        sa.Column("token", sa.String(length=43), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("token", name=op.f("pk_access_tokens")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_access_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_access_tokens_created_at"),
        "access_tokens",
        ["created_at"],
    )


def downgrade() -> None:
    """Drop the access_tokens table."""
    op.drop_index(op.f("ix_access_tokens_created_at"), table_name="access_tokens")
    op.drop_table("access_tokens")
