"""User model — global identity across organisations.

Users are not org-scoped. A single user joins one or more organisations
through ``OrgMembership``. The column set is deliberately compatible with
``fastapi_users_db_sqlalchemy.SQLAlchemyBaseUserTableUUID`` so the auth
library can wire in later without a schema migration.

The email column uses ``CITEXT`` for case-insensitive uniqueness. The
``citext`` extension is installed by ``docker/init-db.sh`` and by the
initial Alembic migration so both fresh and existing databases have it.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Uuid, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from unstash.db.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from unstash.db.models.org_membership import OrgMembership


class User(Base, TimestampMixin):
    """A platform user.

    Authentication is handled by FastAPI-Users at M4; this model defines
    the schema that auth wiring will attach to. ``is_superuser`` denotes
    platform-level admin (rare — for operator use only), not org-level
    admin which is a membership role.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(CITEXT(), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )

    memberships: Mapped[list[OrgMembership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
