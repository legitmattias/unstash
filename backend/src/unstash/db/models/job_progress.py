"""JobProgress model — UI-facing mirror of a Taskiq background job.

Taskiq is the canonical source of truth for task state in Redis; this
table exists so the API layer can surface progress to the frontend
without depending on Taskiq's internal storage and so historical job
records survive Redis evictions.

One row per submitted task, identified by ``task_id`` (Taskiq's task
identifier, kept as text because Taskiq does not standardise on UUID).
``progress_percent`` and ``progress_detail`` are optional fields the
task body updates as it makes progress; ``status`` moves through
queued, running, and one of the terminal states.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from unstash.db.models.base import Base, TimestampMixin


class JobStatus(enum.StrEnum):
    """Lifecycle state of a background job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobProgress(Base, TimestampMixin):
    """UI-facing record of a background job's state."""

    __tablename__ = "job_progress"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "progress_percent IS NULL OR (progress_percent BETWEEN 0 AND 100)",
            name="progress_percent_range",
        ),
        # Worker queue and UI listing: jobs in a given state for an org.
        Index("ix_job_progress_org_id_status", "org_id", "status"),
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
    task_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    task_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'queued'"),
    )
    progress_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(String, nullable=True)
