"""Document model — a single ingested file belonging to an organisation.

A document originates either from a connector sync (Google Drive, Dropbox,
OneDrive) or a manual upload. ``connector_id`` is nullable; the foreign key
to ``connectors`` is added in migration 0003 once that table exists.

Documents move through a parse/index lifecycle tracked by ``status``. The
parsed text is split into ``Chunk`` rows for retrieval.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from unstash.db.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from unstash.db.models.chunk import Chunk
    from unstash.db.models.connector import Connector


class DocumentStatus(enum.StrEnum):
    """Lifecycle state of a document in the ingestion pipeline."""

    PENDING = "pending"
    PARSING = "parsing"
    PARSED = "parsed"
    INDEXED = "indexed"
    FAILED = "failed"


class Document(Base, TimestampMixin):
    """An ingested file, scoped to one organisation."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'parsing', 'parsed', 'indexed', 'failed')",
            name="status_valid",
        ),
        # Listing a single org's documents, newest first.
        Index("ix_documents_org_id_created_at", "org_id", "created_at"),
        # Worker queue scans: documents in a given state for an org.
        Index("ix_documents_org_id_status", "org_id", "status"),
        # Content-hash dedup within an org.
        Index("ix_documents_org_id_content_hash", "org_id", "content_hash"),
        # Prevents re-importing the same connector resource twice. Partial so
        # manual uploads (connector_id IS NULL) are not constrained.
        Index(
            "uq_documents_connector_resource",
            "org_id",
            "connector_id",
            "connector_resource_id",
            unique=True,
            postgresql_where=text("connector_id IS NOT NULL"),
        ),
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
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("connectors.id", ondelete="SET NULL"),
        nullable=True,
    )
    connector_resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    source_uri: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'pending'"),
    )
    parsing_error: Mapped[str | None] = mapped_column(String, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Forward-compat fields (see ADR rationale + spine note): which
    # ingestion pipeline produced this document's chunks, so later
    # phases can apply PII redaction or rebuild eval golden sets
    # without re-ingesting.
    pipeline_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    connector: Mapped[Connector | None] = relationship(back_populates="documents")
