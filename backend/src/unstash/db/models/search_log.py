"""SearchLog model — one row per executed search query.

Append-only analytics. Used to surface popular queries, measure latency
trends, and (eventually) feed click-through signals into ranking.

No ``updated_at``: rows are immutable once written, with the exception of
``clicked_document_id``, which is patched in by the click-event ingestion
path. That update happens via a targeted SQL statement keyed by ``id`` —
not through the ORM — so no row-level timestamp churn is wanted either.

Foreign keys are ``ON DELETE SET NULL`` for ``user_id`` and both document
references: a deleted user or document does not erase the search history,
it just disconnects the rows from the now-missing target. Org deletion
cascades as everywhere else.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from unstash.db.models.base import Base


class SearchLog(Base):
    """A single executed search, with timing and click attribution."""

    __tablename__ = "search_logs"
    __table_args__ = (
        # Primary access pattern: recent searches for a given org.
        Index("ix_search_logs_org_id_created_at", "org_id", "created_at"),
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
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    query: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    top_document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    clicked_document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
