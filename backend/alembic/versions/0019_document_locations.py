"""Document source locations.

Adds ``document_locations``: one row per place a document was found at
its source. A document has one content row and any number of locations,
so a deduplicated upload keeps the path of every copy.

The table is tenant-scoped: it carries ``org_id`` and gets the same
``tenant_isolation`` policy as the tables covered in 0006.

Revision ID: 0019_document_locations
Revises: 0018_llm_calls
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic identifiers.
revision: str = "0019_document_locations"
down_revision: str | None = "0018_llm_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "document_locations"
_POLICY_PREDICATE = "org_id = current_setting('app.current_org_id')::uuid"
_POLICY_NAME = "tenant_isolation"


def upgrade() -> None:
    """Create document_locations with its indexes and tenant policy."""
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), server_default=sa.text("'folder'"), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_locations")),
        sa.CheckConstraint(
            "kind IN ('folder')",
            name=op.f("ck_document_locations_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_document_locations_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_locations_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name=op.f("fk_document_locations_connector_id_connectors"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "uq_document_locations_document_connector_path",
        _TABLE,
        ["document_id", "connector_id", "path"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_document_locations_org_id_document_id",
        _TABLE,
        ["org_id", "document_id"],
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_POLICY_NAME} ON {_TABLE}
            FOR ALL
            TO unstash_app
            USING ({_POLICY_PREDICATE})
            WITH CHECK ({_POLICY_PREDICATE});
        """
    )


def downgrade() -> None:
    """Drop document_locations and its policy."""
    op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {_TABLE};")
    op.drop_index("ix_document_locations_org_id_document_id", _TABLE)
    op.drop_index("uq_document_locations_document_connector_path", _TABLE)
    op.drop_table(_TABLE)
