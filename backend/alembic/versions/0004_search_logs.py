"""Search logs.

Adds the ``search_logs`` table — one row per executed search query, used
for usage analytics and (later) click-through signal for ranking. Rows
are append-only; ``clicked_document_id`` is the only field patched after
insert, via a targeted SQL statement when the click event arrives.

User and document references are ``ON DELETE SET NULL`` so removing
either of those does not destroy the search history, only disconnects
the rows from the now-missing target.

Revision ID: 0004_search_logs
Revises: 0003_connectors
Create Date: 2026-06-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Alembic identifiers.
revision: str = "0004_search_logs"
down_revision: str | None = "0003_connectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the search_logs table."""
    op.create_table(
        "search_logs",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("query", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("top_document_id", sa.Uuid(), nullable=True),
        sa.Column("clicked_document_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_logs")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_search_logs_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_search_logs_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["top_document_id"],
            ["documents.id"],
            name=op.f("fk_search_logs_top_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["clicked_document_id"],
            ["documents.id"],
            name=op.f("fk_search_logs_clicked_document_id_documents"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_search_logs_org_id_created_at",
        "search_logs",
        ["org_id", "created_at"],
    )


def downgrade() -> None:
    """Drop the search_logs table."""
    op.drop_index("ix_search_logs_org_id_created_at", table_name="search_logs")
    op.drop_table("search_logs")
