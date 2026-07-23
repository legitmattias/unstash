"""Cluster model — a discovered document category within one clustering run.

Each cluster belongs to exactly one :class:`ClusteringRun`; re-clustering
creates fresh rows under the new run and re-points ``documents.cluster_id``,
leaving prior runs' clusters in place as history. Documents the algorithm
marks as noise keep ``cluster_id`` NULL.

``label`` is never NULL: it is set to a keyword-derived placeholder at
creation and upgraded when LLM labeling succeeds, with ``label_source``
recording which of the two produced the current value (``user_edited`` is
reserved for the correction loop).
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from unstash.db.models.base import Base, TimestampMixin


class ClusterLabelSource(enum.StrEnum):
    """Where a cluster's current label came from."""

    KEYWORD_FALLBACK = "keyword_fallback"
    LLM = "llm"
    USER_EDITED = "user_edited"


class Cluster(Base, TimestampMixin):
    """A discovered document category, scoped to one organisation."""

    __tablename__ = "clusters"
    __table_args__ = (
        CheckConstraint(
            "label_source IN ('keyword_fallback', 'llm', 'user_edited')",
            name="cluster_label_source_valid",
        ),
        # Clusters of a given run; leads with org_id to match the RLS predicate.
        Index("ix_clusters_org_id_run_id", "org_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("clustering_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    label: Mapped[str] = mapped_column(Text, nullable=False)
    label_source: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'keyword_fallback'"),
    )

    # Top c-TF-IDF terms as a list of {"term": str, "score": float}.
    keywords: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    # Documents closest to the cluster centroid, as UUID strings — input for
    # labeling and the admin overview. A plain list, not FKs: representatives
    # are a sample, and a deleted document must not invalidate the cluster.
    representative_document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    # Document count at creation. ``documents.cluster_id`` only reflects the
    # latest run, so historical cluster sizes are recorded here.
    size: Mapped[int] = mapped_column(Integer, nullable=False)
