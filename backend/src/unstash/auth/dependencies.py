"""Shared FastAPI dependencies for authenticated routes.

The default authentication path is the cookie-based session via
FastAPI-Users. Routes that also accept ``Authorization: Bearer <token>``
should use :func:`current_user_or_token` instead of
:data:`current_active_user`.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unstash.auth.backend import fastapi_users
from unstash.auth.tokens import (
    constant_time_equals,
    hash_token,
    looks_like_unstash_token,
)
from unstash.db.models import ApiToken, User
from unstash.db.session import get_session_unmanaged

logger = structlog.get_logger(__name__)


current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)

# Cookie-auth dependency that returns None if no valid session exists,
# instead of raising 401. Used as a fallback inside
# :func:`current_user_or_token` so the Bearer path can take priority
# without paying the cookie path's 401 first.
_optional_cookie_user = fastapi_users.current_user(active=True, optional=True)


_BEARER_PARTS = 2


def _extract_bearer(request: Request) -> str | None:
    """Return the Bearer credential from the request, or None."""
    header = request.headers.get("Authorization")
    if header is None:
        return None
    parts = header.split(" ", 1)
    if len(parts) != _BEARER_PARTS or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


async def _resolve_token(session: AsyncSession, plaintext: str) -> User | None:
    """Look up the user behind a plaintext token, or None if not valid."""
    if not looks_like_unstash_token(plaintext):
        return None
    candidate_hash = hash_token(plaintext)
    stmt = select(ApiToken).where(ApiToken.token_hash == candidate_hash)
    token_row = (await session.execute(stmt)).scalar_one_or_none()
    # The ``token_hash`` column is unique-indexed so a matching row is
    # already the right one. The constant-time compare is belt-and-
    # suspenders against a future change that broadens the lookup (a
    # range filter, a hash-prefix index, etc.) so the comparison stays
    # explicit at the call site.
    if (
        token_row is None
        or not constant_time_equals(token_row.token_hash, candidate_hash)
        or token_row.revoked_at is not None
        or (token_row.expires_at is not None and token_row.expires_at <= datetime.now(UTC))
    ):
        return None
    user = await session.get(User, token_row.user_id)
    if user is None or not user.is_active:
        return None
    # Best-effort touch of last_used_at. Failure here must not break
    # the request — auth has already succeeded.
    with contextlib.suppress(Exception):
        token_row.last_used_at = datetime.now(UTC)
        await session.commit()
    return user


async def current_user_or_token(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session_unmanaged)],
    cookie_user: Annotated[User | None, Depends(_optional_cookie_user)] = None,
) -> User:
    """Resolve the request's user from a Bearer token or a session cookie.

    Order: Bearer first, cookie second. A Bearer header that fails to
    resolve (malformed, unknown, revoked, expired, deleted user) is a
    hard 401 — we do not silently fall back to the cookie, because that
    would mask token problems and confuse the caller.
    """
    bearer = _extract_bearer(request)
    if bearer is not None:
        user = await _resolve_token(session, bearer)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
            )
        return user
    if cookie_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return cookie_user
