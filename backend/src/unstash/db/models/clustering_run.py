"""ClusteringRun model — one clustering execution over an org's documents.

A run records what the clustering job saw and produced: how many documents
went in, how many clusters (and noise documents) came out, the quality
signals, and the parameters used. ``params`` snapshots the parameters and
library versions, in the same provenance role as
``documents.pipeline_config``.

``document_count`` at the latest successful run is the reference point for
the automatic re-cluster trigger (run again when the org's document count
has doubled since).
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from unstash.db.models.base import Base, TimestampMixin


class ClusteringRunStatus(enum.StrEnum):
    """Lifecycle state of a clustering run."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ClusteringTrigger(enum.StrEnum):
    """What started a clustering run."""

    THRESHOLD = "threshold"
    DOUBLING = "doubling"
    MANUAL = "manual"


class ClusteringRun(Base, TimestampMixin):
    """A clustering execution, scoped to one organisation."""

    __tablename__ = "clustering_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="clustering_run_status_valid",
        ),
        CheckConstraint(
            "triggered_by IN ('threshold', 'doubling', 'manual')",
            name="clustering_run_trigger_valid",
        ),
        # Latest run for an org; leads with org_id to match the RLS predicate.
        Index("ix_clustering_runs_org_id_created_at", "org_id", "created_at"),
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

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'running'"),
    )
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)

    # Documents included in the run (indexed status at run start).
    document_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Outcome fields; NULL until the run succeeds. ``silhouette`` stays NULL
    # when it is undefined (fewer than two clusters).
    cluster_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    noise_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    silhouette: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Parameter snapshot: UMAP/HDBSCAN settings, random_state, library
    # versions. Shape is owned by the clustering service.
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
