"""DocumentMetadata model — extracted structured signals per document.

One row per document holding the best-effort extraction results: dates,
amounts, and (once NER is wired in the worker) named entities. Kept in a
separate table so ``documents`` stays lean and the extraction layer can
evolve without touching the core row.

``org_id`` is denormalised from the parent document so the Row-Level
Security predicate filters on a local column, matching every other
tenant-scoped table.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from unstash.db.models.base import Base, TimestampMixin


class DocumentMetadata(Base, TimestampMixin):
    """Extracted metadata for one document (1:1, upserted on re-extraction)."""

    __tablename__ = "document_metadata"
    __table_args__ = (
        UniqueConstraint("document_id"),
        # Leads with org_id to match the RLS predicate.
        Index("ix_document_metadata_org_id_document_id", "org_id", "document_id"),
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
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Lists of extraction records; shapes are owned by the extractors in
    # unstash.documents.metadata / unstash.documents.ner. ``entities``
    # stays NULL until NER runs in the worker.
    entities: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    dates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    amounts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    extractor_version: Mapped[str] = mapped_column(Text, nullable=False)
