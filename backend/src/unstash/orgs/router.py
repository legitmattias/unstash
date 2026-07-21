"""Org-scoped routes: the user-facing API mounted under /api/orgs/{slug}/."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from unstash.db.models import Organisation, OrgMembership
from unstash.orgs.dependencies import CurrentUserDep, OrgContextDep, UserContextDep
from unstash.orgs.schemas import MembershipRead, OrgSummary

orgs_router = APIRouter()


@orgs_router.get("/me/organisations", response_model=list[OrgSummary])
async def list_my_organisations(ctx: UserContextDep) -> list[OrgSummary]:
    """Return every organisation the caller belongs to, with their role.

    Reads ``org_memberships`` under the ``own_memberships_read`` policy,
    which scopes rows to the caller regardless of org; ``organisations``
    is global. Ordered by name for a stable picker.
    """
    stmt = (
        select(Organisation.slug, Organisation.name, OrgMembership.role)
        .join(OrgMembership, OrgMembership.org_id == Organisation.id)
        .where(OrgMembership.user_id == ctx.user_id)
        .order_by(Organisation.name)
    )
    rows = (await ctx.session.execute(stmt)).all()
    return [OrgSummary(slug=row.slug, name=row.name, role=row.role) for row in rows]


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
