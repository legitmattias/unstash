"""Document metadata table.

Adds ``document_metadata`` — one row per document holding best-effort
extracted signals (dates, amounts, and later named entities) for search
filters and faceting. Separate from ``documents`` so the extraction layer
can evolve without touching the core row.

Tenant-scoped: carries ``org_id``, gets the same ``tenant_isolation``
policy as the tables covered in 0006 (new tables enable their own RLS).

Revision ID: 0012_document_metadata
Revises: 0011_chunks_emb_null
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# Alembic identifiers.
revision: str = "0012_document_metadata"
down_revision: str | None = "0011_chunks_emb_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_PREDICATE = "org_id = current_setting('app.current_org_id')::uuid"
_POLICY_NAME = "tenant_isolation"


def upgrade() -> None:
    """Create document_metadata with its index and RLS policy."""
    op.create_table(
        "document_metadata",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("entities", JSONB(), nullable=True),
        sa.Column("dates", JSONB(), nullable=True),
        sa.Column("amounts", JSONB(), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_metadata")),
        sa.UniqueConstraint(
            "document_id",
            name=op.f("uq_document_metadata_document_id"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_document_metadata_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_metadata_document_id_documents"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_document_metadata_org_id_document_id",
        "document_metadata",
        ["org_id", "document_id"],
    )
    op.execute("ALTER TABLE document_metadata ENABLE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_POLICY_NAME} ON document_metadata
            FOR ALL
            TO unstash_app
            USING ({_POLICY_PREDICATE})
            WITH CHECK ({_POLICY_PREDICATE});
        """
    )


def downgrade() -> None:
    """Drop the policy and the document_metadata table."""
    op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON document_metadata;")
    op.drop_index("ix_document_metadata_org_id_document_id", "document_metadata")
    op.drop_table("document_metadata")
