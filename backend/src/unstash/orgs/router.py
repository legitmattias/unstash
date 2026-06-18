"""Org-scoped routes: the user-facing API mounted under /api/orgs/{slug}/."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text

from unstash.auth.dependencies import current_active_user
from unstash.db.models import Organisation, OrgMembership, User
from unstash.db.session import get_sessionmaker
from unstash.orgs.schemas import MembershipRead

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


CurrentUserDep = Annotated[User, Depends(current_active_user)]


async def get_org_scoped_session(
    slug: str,
    user: CurrentUserDep,
) -> AsyncIterator[AsyncSession]:
    """Yield a session bound to the request's org context.

    Resolves ``{slug}`` to an ``org_id`` via the ``organisations`` table
    (which is not RLS-protected, so the lookup runs without any GUC
    context), opens a transaction, sets ``app.current_org_id`` for the
    transaction via ``set_config(..., true)``, then verifies the
    authenticated user is a member of the org. The verification query
    benefits from the same RLS policy being set up — it can only return
    memberships for the org the GUC points at, so a row for the current
    user implies "is a member".

    Raises:
        HTTPException(404): the slug does not resolve to an organisation.
        HTTPException(403): the user is not a member of the org.

    The transaction commits on clean route return and rolls back on any
    exception, so partial writes from a failing handler are never
    persisted.
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

        # ``set_config(name, value, is_local=true)`` is the function form
        # of ``SET LOCAL`` and accepts query parameters, which ``SET LOCAL``
        # itself does not. ``is_local=true`` confines the GUC to the
        # current transaction so it cannot leak to a future request when
        # the connection is returned to the pool.
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

        yield session


OrgScopedSessionDep = Annotated["AsyncSession", Depends(get_org_scoped_session)]


orgs_router = APIRouter()


@orgs_router.get(
    "/orgs/{slug}/me",
    response_model=MembershipRead,
)
async def get_my_membership(
    session: OrgScopedSessionDep,
    user: CurrentUserDep,
) -> OrgMembership:
    """Return the caller's membership in this organisation.

    The query reads from ``org_memberships``, an RLS-protected table.
    ``app.current_org_id`` was set by ``get_org_scoped_session``, so RLS
    automatically scopes the query to this org. We can therefore filter
    by ``user_id`` alone and trust the database to handle org scoping.
    """
    stmt = select(OrgMembership).where(OrgMembership.user_id == user.id)
    membership = (await session.execute(stmt)).scalar_one_or_none()
    if membership is None:
        # The org-scoped dependency already verified membership, so this
        # is "shouldn't happen" — but defensive code stays defensive.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return membership
