"""AccessToken model — server-side session tokens for FastAPI-Users.

Each row represents an authenticated session. The cookie value is the
``token`` column. The application looks up the row at every request to
validate the session, which allows server-side invalidation on logout.

Tokens are not org-scoped. They identify a user; the org context is
established per-request by middleware via the URL slug + org_memberships.
RLS therefore does not apply to this table — see the M2 Phase B plan's
treatment of the ``users`` table for the same reasoning.
"""

from __future__ import annotations

import uuid

from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyBaseAccessTokenTableUUID
from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from unstash.db.models.base import Base


class AccessToken(SQLAlchemyBaseAccessTokenTableUUID, Base):
    """A server-side session token used by the cookie-based auth backend."""

    __tablename__ = "access_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(  # pyright: ignore[reportIncompatibleVariableOverride]
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
