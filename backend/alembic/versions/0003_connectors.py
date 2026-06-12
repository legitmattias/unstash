"""Connectors, plus the deferred documents.connector_id foreign key.

Adds the ``connectors`` table and finally attaches the foreign key from
``documents.connector_id`` (created nullable in 0002) to ``connectors.id``.
Deleting a connector sets the referencing documents' ``connector_id`` to
NULL rather than removing the documents.

Revision ID: 0003_connectors
Revises: 0002_documents
Create Date: 2026-05-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Alembic identifiers.
revision: str = "0003_connectors"
down_revision: str | None = "0002_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the connectors table and wire the documents FK."""
    op.create_table(
        "connectors",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("credentials_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connectors")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_connectors_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "provider IN ('google_drive', 'dropbox', 'onedrive', 'manual_upload')",
            name=op.f("ck_connectors_provider_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'failed', 'disconnected')",
            name=op.f("ck_connectors_status_valid"),
        ),
    )
    op.create_index(
        "ix_connectors_org_id_status",
        "connectors",
        ["org_id", "status"],
    )

    # Attach the FK deferred from migration 0002.
    op.create_foreign_key(
        op.f("fk_documents_connector_id_connectors"),
        "documents",
        "connectors",
        ["connector_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop the documents FK and the connectors table."""
    op.drop_constraint(
        op.f("fk_documents_connector_id_connectors"),
        "documents",
        type_="foreignkey",
    )
    op.drop_index("ix_connectors_org_id_status", table_name="connectors")
    op.drop_table("connectors")
