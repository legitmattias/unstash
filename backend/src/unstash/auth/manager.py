"""UserManager — handles password validation, hashing, and lifecycle hooks."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated

import structlog
from fastapi import Depends
from fastapi_users import BaseUserManager, InvalidPasswordException, UUIDIDMixin
from fastapi_users.password import PasswordHelper
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from unstash.auth.db import get_user_db
from unstash.config import get_settings
from unstash.db.models import User

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import Request
    from fastapi_users.password import PasswordHelperProtocol

logger = structlog.get_logger(__name__)


_PASSWORD_HASH = PasswordHash((Argon2Hasher(),))
_MIN_PASSWORD_LENGTH = 8


def _password_helper() -> PasswordHelperProtocol:
    """Return a PasswordHelper backed by Argon2id only."""
    return PasswordHelper(_PASSWORD_HASH)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):  # pyright: ignore[reportInvalidTypeArguments]
    """Application-level user manager.

    Password reset and email verification flows are not wired in this
    milestone; the secrets below are still required by FastAPI-Users for
    method signatures and are sourced from ``settings.session_secret``.

    The ``# pyright: ignore`` works around a known FastAPI-Users vs
    SQLAlchemy ORM friction — see ``db/models/user.py``.
    """

    def __init__(
        self,
        user_db: SQLAlchemyUserDatabase[User, uuid.UUID],  # pyright: ignore[reportInvalidTypeArguments]
        password_helper: PasswordHelperProtocol,
    ) -> None:
        """Initialise the manager and bind reset/verification token secrets."""
        super().__init__(user_db, password_helper)
        settings = get_settings()
        self.reset_password_token_secret = settings.session_secret
        self.verification_token_secret = settings.session_secret

    async def validate_password(  # pyright: ignore[reportIncompatibleMethodOverride, reportImplicitOverride]
        self,
        password: str,
        user: User,
    ) -> None:
        """Reject too-short passwords or passwords containing the email local part."""
        if len(password) < _MIN_PASSWORD_LENGTH:
            raise InvalidPasswordException(
                reason=f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.",
            )
        if user.email and user.email.split("@", 1)[0].lower() in password.lower():
            raise InvalidPasswordException(
                reason="Password cannot contain the local part of the email."
            )

    async def on_after_login(  # pyright: ignore[reportImplicitOverride]
        self,
        user: User,
        request: Request | None = None,
        response: object | None = None,
    ) -> None:
        """Log a structured event when a user successfully authenticates."""
        del request, response
        logger.info("user_login", user_id=str(user.id))

    async def on_after_logout(
        self,
        user: User,
    ) -> None:
        """Log a structured event when a user logs out (non-standard hook)."""
        logger.info("user_logout", user_id=str(user.id))


UserDbDep = Annotated[
    SQLAlchemyUserDatabase[User, uuid.UUID],  # pyright: ignore[reportInvalidTypeArguments]
    Depends(get_user_db),
]


async def get_user_manager(
    user_db: UserDbDep,
) -> AsyncIterator[UserManager]:
    """Yield a UserManager bound to the request session."""
    yield UserManager(user_db, _password_helper())
