"""FastAPI-Users database dependencies — user store and access-token store."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from unstash.db.models import AccessToken, User
from unstash.db.session import get_session_unmanaged

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator

SessionDep = Annotated[AsyncSession, Depends(get_session_unmanaged)]


async def get_user_db(
    session: SessionDep,
) -> AsyncIterator[SQLAlchemyUserDatabase[User, uuid.UUID]]:  # pyright: ignore[reportInvalidTypeArguments]
    """Yield a SQLAlchemyUserDatabase bound to the request session.

    The ``# pyright: ignore`` works around a known FastAPI-Users
    ``UserProtocol`` vs SQLAlchemy ``Mapped[T]`` mismatch — see
    ``db/models/user.py`` for the rationale. The library is correct at
    runtime; pyright lacks the SQLAlchemy plugin that mypy has.
    """
    yield SQLAlchemyUserDatabase(session, User)  # pyright: ignore[reportArgumentType]


async def get_access_token_db(
    session: SessionDep,
) -> AsyncIterator[SQLAlchemyAccessTokenDatabase[AccessToken]]:
    """Yield a SQLAlchemyAccessTokenDatabase bound to the request session."""
    yield SQLAlchemyAccessTokenDatabase(session, AccessToken)
