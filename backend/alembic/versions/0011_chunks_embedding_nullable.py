"""Make chunks.embedding nullable.

Closes ``notes/open-topics-and-edge-cases.md`` section E.3 (referenced
in the M3 plan): the original M2 schema declared ``chunks.embedding``
as NOT NULL, anticipating that every chunk would always carry an
embedding. M3-B (this PR) decouples parsing from embedding — the
worker writes chunks during parsing with NULL embeddings, and the
embed step in M3-C fills them in later.

The DiskANN index on ``embedding`` is unaffected: pgvector indexes
naturally skip NULL values, so queries against the index return only
rows that have been embedded. Querying a NULL-embedding chunk through
the hybrid-search path is therefore a no-op, which is the right
behaviour during the parsed-but-not-yet-embedded window.

Revision ID: 0011_chunks_emb_null
Revises: 0010_doc_provenance
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# Jina v4 native dimension. Must match the model's column definition.
EMBEDDING_DIM = 2048

revision: str = "0011_chunks_emb_null"
down_revision: str | None = "0010_doc_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow NULL in chunks.embedding so parsing can run ahead of embedding."""
    op.alter_column(
        "chunks",
        "embedding",
        existing_type=Vector(EMBEDDING_DIM),
        nullable=True,
    )


def downgrade() -> None:
    """Restore the NOT NULL constraint, dropping any NULL-embedding rows.

    This downgrade is **destructive** when the database holds rows
    with NULL embeddings — typically the parsed-but-not-yet-embedded
    window introduced by M3-B. The DELETE here removes those rows so
    the ALTER can succeed; in production an operator should re-run
    embedding generation to recreate them.

    The destructive behaviour is required for two reasons. First,
    tests run downgrade-to-base + upgrade-to-head between each test
    case and would otherwise fail repeatedly. Second, an operator
    consciously running this downgrade has already decided that
    NULL-embedding chunks should not exist; forcing manual cleanup
    on top of that is friction without safety benefit. The runbook
    notes this.
    """
    op.execute("DELETE FROM chunks WHERE embedding IS NULL")
    op.alter_column(
        "chunks",
        "embedding",
        existing_type=Vector(EMBEDDING_DIM),
        nullable=False,
    )
