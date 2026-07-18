"""Add pipeline provenance columns to documents.

These two columns capture which ingestion pipeline produced a
document's chunks: a free-form ``pipeline_version`` string (e.g.
``parser=docling@2.4 chunker=structure-aware-v1``) and a JSONB
``pipeline_config`` snapshot of the relevant config (chunk size,
overlap, OCR settings, etc.).

Forward-compatibility rationale: later work may need to apply PII
redaction retroactively, or rebuild eval golden sets, without
re-ingesting. Both operations need to know "which pipeline shape
produced these chunks" to interpret them correctly. Without these
columns, anything depending on per-chunk provenance would force a
re-ingestion.

Both columns are nullable. Existing documents (none in production at
the time of this migration) carry NULL until re-ingested.

Revision ID: 0010_doc_provenance
Revises: 0009_api_tokens
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_doc_provenance"
down_revision: str | None = "0009_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add pipeline_version and pipeline_config columns to documents."""
    op.add_column(
        "documents",
        sa.Column("pipeline_version", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "pipeline_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the pipeline_version and pipeline_config columns."""
    op.drop_column("documents", "pipeline_config")
    op.drop_column("documents", "pipeline_version")
