"""Authentication backend: cookie transport + database-backed session strategy."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi_users import FastAPIUsers
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy.db import (
    AccessTokenDatabase,
    DatabaseStrategy,
)

from unstash.auth.db import get_access_token_db
from unstash.auth.manager import get_user_manager
from unstash.config import get_settings
from unstash.db.models import AccessToken, User

_SESSION_LIFETIME_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _cookie_transport() -> CookieTransport:
    """Build the cookie transport with environment-aware secure flag."""
    settings = get_settings()
    is_production_like = settings.environment in ("production", "staging")
    return CookieTransport(
        cookie_name="unstash_session",
        cookie_max_age=_SESSION_LIFETIME_SECONDS,
        cookie_secure=is_production_like,
        cookie_httponly=True,
        cookie_samesite="lax",
        cookie_path="/",
    )


cookie_transport = _cookie_transport()

AccessTokenDbDep = Annotated[
    AccessTokenDatabase[AccessToken],
    Depends(get_access_token_db),
]


def get_database_strategy(
    access_token_db: AccessTokenDbDep,
) -> DatabaseStrategy[User, uuid.UUID, AccessToken]:  # pyright: ignore[reportInvalidTypeArguments]
    """Build a DatabaseStrategy bound to the request session.

    Pyright cannot prove ``User`` satisfies FastAPI-Users' ``UserProtocol``
    because the protocol uses bare ``str``/``bool`` field types while
    SQLAlchemy 2.0 ORM uses ``Mapped[T]`` — the two are equivalent at
    runtime but pyright lacks the SQLAlchemy plugin that resolves this.
    """
    return DatabaseStrategy(  # pyright: ignore[reportUnknownVariableType]
        access_token_db,
        lifetime_seconds=_SESSION_LIFETIME_SECONDS,
    )


auth_backend: AuthenticationBackend[User, uuid.UUID] = AuthenticationBackend(  # pyright: ignore[reportInvalidTypeArguments, reportUnknownVariableType]
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_database_strategy,  # pyright: ignore[reportArgumentType]
)


fastapi_users = FastAPIUsers[User, uuid.UUID](  # pyright: ignore[reportInvalidTypeArguments]
    get_user_manager,
    [auth_backend],
)
