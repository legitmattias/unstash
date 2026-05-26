"""OrgMembership — many-to-many join between users and organisations.

Carries the user's role within the org. Role-based authorisation reads
this row to decide what a user can do inside one of their organisations.
A user can belong to many orgs with different roles in each.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from unstash.db.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from unstash.db.models.organisation import Organisation
    from unstash.db.models.user import User


class OrgRole(enum.StrEnum):
    """Role a user holds inside an organisation."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class OrgMembership(Base, TimestampMixin):
    """Membership of a user in an organisation, with a role."""

    __tablename__ = "org_memberships"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')",
            name="role_valid",
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
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)

    organisation: Mapped[Organisation] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")
