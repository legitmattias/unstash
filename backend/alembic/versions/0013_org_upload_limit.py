"""Per-org daily upload limit.

Adds ``organisations.daily_upload_limit`` — uploads accepted per UTC day.
NULL means unlimited (the pilot default); set per org by the operator
until a billing tier owns the value.

Revision ID: 0013_org_upload_limit
Revises: 0012_document_metadata
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Alembic identifiers.
revision: str = "0013_org_upload_limit"
down_revision: str | None = "0012_document_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable daily_upload_limit column."""
    op.add_column(
        "organisations",
        sa.Column("daily_upload_limit", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop the daily_upload_limit column."""
    op.drop_column("organisations", "daily_upload_limit")
