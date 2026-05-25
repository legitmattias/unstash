"""Organisation model — the tenant boundary.

Every other table that holds tenant data carries an ``org_id`` foreign
key to this table. Row-Level Security policies (introduced in a later
migration) use the same ``org_id`` value as the predicate so the
application role cannot read or write rows for other organisations.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from unstash.db.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from unstash.db.models.org_membership import OrgMembership


class Organisation(Base, TimestampMixin):
    """A tenant of the platform.

    The slug is the URL identity (``/orgs/{slug}/...``) and is constrained
    to lowercase alphanumerics with internal hyphens. The locale drives
    default UI language and Postgres text-search stemming.
    """

    __tablename__ = "organisations"
    __table_args__ = (
        CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]*[a-z0-9]$'",
            name="slug_format",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    locale: Mapped[str] = mapped_column(
        String,
        nullable=False,
        server_default="sv-SE",
    )

    memberships: Mapped[list[OrgMembership]] = relationship(
        back_populates="organisation",
        cascade="all, delete-orphan",
    )
