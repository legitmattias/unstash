"""Narrow chunks.embedding from 2048 to 1024 dimensions.

Vectors of different widths, or from different models, are not
comparable, so the stored embeddings are discarded rather than
converted. Indexed documents that have chunks return to ``parsed``;
they are searchable again once their chunks have been re-embedded (see
the re-embed runbook). Documents without chunks keep their status, as
there is nothing to embed.

Revision ID: 0020_embedding_width_1024
Revises: 0019_document_locations
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0020_embedding_width_1024"
down_revision: str | None = "0019_document_locations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_WIDTH = 2048
_NEW_WIDTH = 1024


def _resize(width: int) -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_diskann")
    op.execute("UPDATE chunks SET embedding = NULL WHERE embedding IS NOT NULL")
    op.execute(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({width})")
    op.execute(
        "CREATE INDEX ix_chunks_embedding_diskann "
        "ON chunks USING diskann (embedding vector_cosine_ops)"
    )
    op.execute(
        "UPDATE documents SET status = 'parsed', indexed_at = NULL "
        "WHERE status = 'indexed' "
        "AND EXISTS (SELECT 1 FROM chunks WHERE chunks.document_id = documents.id)"
    )


def upgrade() -> None:
    """Discard stored embeddings and narrow the column to 1024 dimensions."""
    _resize(_NEW_WIDTH)


def downgrade() -> None:
    """Discard stored embeddings and widen the column back to 2048 dimensions."""
    _resize(_OLD_WIDTH)
