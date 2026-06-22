"""ApiToken model — Bearer-token credentials for programmatic access.

See migration 0009_api_tokens for column rationale and ADR 0006 for the
storage-format decision (SHA-256 over Argon2 for high-entropy tokens).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, LargeBinary, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from unstash.db.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from unstash.db.models.organisation import Organisation
    from unstash.db.models.user import User


class ApiToken(Base, TimestampMixin):
    """A long-lived Bearer credential for programmatic access.

    The plaintext token is shown to the operator once at creation and
    never persisted. ``token_hash`` holds the SHA-256 digest of the
    plaintext; verification at auth time is a single indexed lookup
    plus a constant-time comparison.

    ``org_id`` is optional. When set, the token only works for requests
    whose org context (URL slug) matches; when null, the token is
    usable in any org the user is a member of.

    ``last_used_at`` is updated best-effort on each successful auth.
    Setting it is not blocking — the request returns first, the update
    is fire-and-forget.
    """

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=True,
    )
    token_hash: Mapped[bytes] = mapped_column(
        LargeBinary(length=32),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship()
    organisation: Mapped[Organisation | None] = relationship()
