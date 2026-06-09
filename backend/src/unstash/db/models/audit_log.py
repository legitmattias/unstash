"""AuditLog model — append-only record of org-scoped actions.

One row per auditable event: document uploads, member adds, role changes,
connector connects, etc. Events are categorised by a free-form ``action``
string (e.g. document.uploaded, org.member_added) so new event types do
not require migrations.

Pre-org-creation events (signup, password reset) are not logged here —
they are surfaced at the OS / Sentry level. Every event in this table is
scoped to exactly one organisation, which is why ``org_id`` is NOT NULL.

Rows are immutable once written; there is no ``updated_at``. The
``metadata`` JSONB column carries event-specific structured data
without inviting a wide, sparsely-populated column set.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from unstash.db.models.base import Base


class AuditLog(Base):
    """An append-only audit record for one org-scoped action."""

    __tablename__ = "audit_log"
    __table_args__ = (
        # Org-scoped audit listing, newest first.
        Index("ix_audit_log_org_id_created_at", "org_id", "created_at"),
        # "Show me everything this actor did" — security investigations,
        # GDPR data-subject access requests.
        Index("ix_audit_log_actor_user_id_created_at", "actor_user_id", "created_at"),
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
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    audit_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
