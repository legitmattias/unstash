"""Ranking-feedback columns on search_logs.

Adds the signal needed to learn from and evaluate ranking:

  - ``results`` — the ordered result set shown to the user, one entry per
    document with its rank and scores. Without positions, click data
    cannot support position-bias correction, pairwise preferences, or an
    honest CTR@k.
  - ``ranking_config`` — which configuration produced the results
    (fusion parameters, backends, model identifiers, degrade flags), so a
    click can be attributed to the config that earned it.
  - ``clicks`` — appended click events with position and timestamp, so a
    second click no longer overwrites the first.

All three are JSONB with empty defaults, so existing rows migrate
without backfill. ``clicked_document_id`` stays as a convenience
denormalisation of the latest click, backed by the full ``clicks`` list.

Revision ID: 0015_search_log_feedback
Revises: 0014_bm25_sv_stem
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Alembic identifiers.
revision: str = "0015_search_log_feedback"
down_revision: str | None = "0014_bm25_sv_stem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the results, ranking_config, and clicks JSONB columns."""
    op.add_column(
        "search_logs",
        sa.Column(
            "results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "search_logs",
        sa.Column(
            "ranking_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "search_logs",
        sa.Column(
            "clicks",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop the ranking-feedback columns."""
    op.drop_column("search_logs", "clicks")
    op.drop_column("search_logs", "ranking_config")
    op.drop_column("search_logs", "results")
