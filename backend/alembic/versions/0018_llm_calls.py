"""LLM call audit table.

Adds ``llm_calls`` — one immutable row per LLM API call (ADR 0011):
purpose, model, token usage, cost, latency, outcome. Org-scoped with the
standard ``tenant_isolation`` policy.

Revision ID: 0018_llm_calls
Revises: 0017_clustering
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic identifiers.
revision: str = "0018_llm_calls"
down_revision: str | None = "0017_clustering"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_PREDICATE = "org_id = current_setting('app.current_org_id')::uuid"
_POLICY_NAME = "tenant_isolation"


def upgrade() -> None:
    """Create llm_calls with its index and RLS policy."""
    op.create_table(
        "llm_calls",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
        sa.CheckConstraint(
            "outcome IN ('ok', 'error')",
            name=op.f("ck_llm_calls_llm_call_outcome_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_llm_calls_org_id_organisations"),
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_llm_calls_org_id_created_at", "llm_calls", ["org_id", "created_at"])
    op.execute("ALTER TABLE llm_calls ENABLE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_POLICY_NAME} ON llm_calls
            FOR ALL
            TO unstash_app
            USING ({_POLICY_PREDICATE})
            WITH CHECK ({_POLICY_PREDICATE});
        """
    )


def downgrade() -> None:
    """Drop the policy and the llm_calls table."""
    op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON llm_calls;")
    op.drop_index("ix_llm_calls_org_id_created_at", "llm_calls")
    op.drop_table("llm_calls")
