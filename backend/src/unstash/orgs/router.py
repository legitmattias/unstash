"""Org-scoped routes: the user-facing API mounted under /api/orgs/{slug}/."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from unstash.db.models import OrgMembership
from unstash.orgs.dependencies import CurrentUserDep, OrgContextDep
from unstash.orgs.schemas import MembershipRead

orgs_router = APIRouter()


@orgs_router.get(
    "/orgs/{slug}/me",
    response_model=MembershipRead,
)
async def get_my_membership(
    ctx: OrgContextDep,
    user: CurrentUserDep,
) -> OrgMembership:
    """Return the caller's membership in this organisation.

    Reads from ``org_memberships``, an RLS-protected table.
    ``app.current_org_id`` was set by the org-scoping dependency, so
    RLS automatically scopes the query to this org. We can therefore
    filter by ``user_id`` alone and trust the database for org scoping.
    """
    stmt = select(OrgMembership).where(OrgMembership.user_id == user.id)
    membership = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if membership is None:
        # The org-scoping dependency already verified membership, so
        # this is "shouldn't happen" — defensive only.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return membership
