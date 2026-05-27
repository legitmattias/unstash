"""Connector model — a configured source that feeds documents into an org.

A connector is an authenticated link to an external storage provider
(Google Drive, Dropbox, OneDrive) or the built-in manual-upload path.
OAuth tokens are stored encrypted in ``credentials_encrypted``; the
encryption key comes from the ``encryption_key`` secret, never the database.

Documents reference their originating connector via ``documents.connector_id``
(FK added in migration 0003). Deleting a connector sets that FK to NULL
rather than cascading — the already-ingested documents remain valid.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from unstash.db.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from unstash.db.models.document import Document


class ConnectorProvider(enum.StrEnum):
    """External source a connector links to."""

    GOOGLE_DRIVE = "google_drive"
    DROPBOX = "dropbox"
    ONEDRIVE = "onedrive"
    MANUAL_UPLOAD = "manual_upload"


class ConnectorStatus(enum.StrEnum):
    """Operational state of a connector."""

    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"
    DISCONNECTED = "disconnected"


class Connector(Base, TimestampMixin):
    """An authenticated source of documents for one organisation."""

    __tablename__ = "connectors"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('google_drive', 'dropbox', 'onedrive', 'manual_upload')",
            name="provider_valid",
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'failed', 'disconnected')",
            name="status_valid",
        ),
        Index("ix_connectors_org_id_status", "org_id", "status"),
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
    provider: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    credentials_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default=text("'active'"),
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)

    documents: Mapped[list[Document]] = relationship(back_populates="connector")
