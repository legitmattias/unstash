"""Swedish stemming on the chunks BM25 index.

Recreates ``ix_chunks_text_bm25`` with ``pdb.icu('stemmer=swedish')`` —
ICU segmentation as before, plus Swedish stemming so inflected forms
match (avgiften/avgifter → avgift). Measured on the retrieval golden set:
BM25 nDCG@10 0.447 → 0.500 overall, with the largest gains on Swedish
semantic/decision queries (see evals/retrieval/reports/). English text in
mixed documents passes through the stemmer mostly untouched; English
keyword recall was ~zero on this index before and after.

Revision ID: 0014_bm25_sv_stem
Revises: 0013_org_upload_limit
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0014_bm25_sv_stem"
down_revision: str | None = "0013_org_upload_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STEMMED = (
    "CREATE INDEX ix_chunks_text_bm25 ON chunks "
    "USING bm25 (id, (text::pdb.icu('stemmer=swedish')), org_id) "
    "WITH (key_field='id')"
)
_ICU = (
    "CREATE INDEX ix_chunks_text_bm25 ON chunks "
    "USING bm25 (id, (text::pdb.icu), org_id) "
    "WITH (key_field='id')"
)


def upgrade() -> None:
    """Recreate the BM25 index with Swedish stemming."""
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_bm25")
    op.execute(_STEMMED)


def downgrade() -> None:
    """Restore the unstemmed ICU BM25 index."""
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_bm25")
    op.execute(_ICU)
