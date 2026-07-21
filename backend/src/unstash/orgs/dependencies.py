"""Shared org-scoping dependency for routes under ``/api/orgs/{slug}/``.

Lives in its own module so other resource routers (documents, jobs,
search results, etc.) can depend on it without circular-import
problems with the orgs router itself.

The dependency yields an :class:`OrgContext` — a small dataclass
carrying the database session (inside a transaction with
``app.current_org_id`` set) and the resolved ``org_id`` UUID. Routes
that only need the session use ``ctx.session``; routes that need the
tenant id for inserts (e.g. document uploads) use ``ctx.org_id``
without a second slug lookup.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, text

from unstash.auth.dependencies import BEARER_ORG_STATE, current_user_or_token
from unstash.db.models import Organisation, OrgMembership, User
from unstash.db.session import get_sessionmaker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


CurrentUserDep = Annotated[User, Depends(current_user_or_token)]

# Sentinel org for user-level requests that carry no org context. It sets
# app.current_org_id to a valid value so the org-scoped tenant_isolation
# policy evaluates to false rather than raising on an unset GUC, while the
# own_memberships_read policy scopes reads to the caller.
_NIL_ORG_ID = "00000000-0000-0000-0000-000000000000"


@dataclass(slots=True)
class OrgContext:
    """A request-scoped org context: the active session and tenant id."""

    session: AsyncSession
    org_id: uuid.UUID


@dataclass(slots=True)
class UserContext:
    """A request-scoped user context for user-level (cross-org) routes."""

    session: AsyncSession
    user_id: uuid.UUID


async def get_user_context(user: CurrentUserDep) -> AsyncIterator[UserContext]:
    """Yield a session scoped to the caller for user-level routes.

    Opens a transaction and sets ``app.current_user_id`` so the
    ``own_memberships_read`` policy exposes the caller's membership rows
    across orgs. ``app.current_org_id`` is set to the nil UUID to keep the
    org-scoped ``tenant_isolation`` policy from raising on an unset GUC.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)").bindparams(
                org_id=_NIL_ORG_ID,
            ),
        )
        await session.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)").bindparams(
                user_id=str(user.id),
            ),
        )
        yield UserContext(session=session, user_id=user.id)


UserContextDep = Annotated[UserContext, Depends(get_user_context)]


async def get_org_context(
    request: Request,
    slug: str,
    user: CurrentUserDep,
) -> AsyncIterator[OrgContext]:
    """Yield an :class:`OrgContext` bound to the request's org.

    Resolves ``{slug}`` to ``org_id`` (``organisations`` is global, not
    RLS-protected, so the lookup works without context), opens a
    transaction, sets ``app.current_org_id`` for the transaction via
    ``set_config(..., true)``, verifies user membership via the now
    RLS-narrowed query against ``org_memberships``, then yields. The
    transaction commits on clean exit and rolls back on exception.

    An org-scoped Bearer token (``ApiToken.org_id`` set) may only reach
    its own org; a request whose slug resolves to a different org is
    rejected with 403.

    Raises:
        HTTPException(403): an org-scoped token is used against a
            different org, or the user is not a member of the org.
        HTTPException(404): the slug does not resolve to an organisation.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        org_id_stmt = select(Organisation.id).where(Organisation.slug == slug)
        org_id = (await session.execute(org_id_stmt)).scalar_one_or_none()
        if org_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organisation not found.",
            )

        token_org_id = getattr(request.state, BEARER_ORG_STATE, None)
        if token_org_id is not None and token_org_id != org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token is not scoped to this organisation.",
            )

        await session.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)").bindparams(
                org_id=str(org_id),
            ),
        )

        membership_stmt = select(OrgMembership.id).where(OrgMembership.user_id == user.id)
        if (await session.execute(membership_stmt)).scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not a member of this organisation.",
            )

        yield OrgContext(session=session, org_id=org_id)


OrgContextDep = Annotated[OrgContext, Depends(get_org_context)]
