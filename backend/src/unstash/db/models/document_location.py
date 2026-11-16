"""DocumentLocation model — a place where a document was found at its source.

A document's identity is its content; the same content is routinely filed
in more than one place. Each row here records one such place, so a
deduplicated upload adds a location to the existing document instead of
losing where the copy was filed.

``path`` is descriptive metadata only. It is never used to address the
stored file, which lives under a server-chosen directory with a sanitised
leaf name.

``org_id`` is denormalised from the parent document so the Row-Level
Security predicate filters on a local column, matching every other
tenant-scoped table.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from unstash.db.models.base import Base, TimestampMixin


class LocationKind(enum.StrEnum):
    """What kind of container a location's path refers to."""

    FOLDER = "folder"


class DocumentLocation(Base, TimestampMixin):
    """One source location of a document (many per document)."""

    __tablename__ = "document_locations"
    __table_args__ = (
        CheckConstraint("kind IN ('folder')", name="kind_valid"),
        # The same path recorded twice for one document is one location.
        # NULLs compare equal so manual uploads (connector_id IS NULL) are
        # covered by the same constraint as connector documents.
        Index(
            "uq_document_locations_document_connector_path",
            "document_id",
            "connector_id",
            "path",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        # Leads with org_id to match the RLS predicate.
        Index("ix_document_locations_org_id_document_id", "org_id", "document_id"),
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
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("connectors.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'folder'"),
    )
    # Normalised by unstash.documents.locations.normalise_source_path:
    # forward slashes, no leading slash, file name as the last segment.
    path: Mapped[str] = mapped_column(Text, nullable=False)
    # Modification time reported by the uploading client or the source
    # system. Unverified; a signal for date extraction, not a fact.
    source_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
