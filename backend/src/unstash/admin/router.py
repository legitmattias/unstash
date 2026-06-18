"""Admin (superuser-only) router for user and membership management."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_users.exceptions import InvalidPasswordException, UserAlreadyExists
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from unstash.admin.schemas import AdminUserCreate, MembershipCreate, MembershipRead
from unstash.auth.dependencies import current_superuser
from unstash.auth.manager import get_user_manager
from unstash.auth.schemas import UserCreate, UserRead
from unstash.db.models import Organisation, OrgMembership, User
from unstash.db.session import get_admin_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from unstash.auth.manager import UserManager

# Admin routes use the BYPASSRLS-scoped admin engine. Never substitute
# get_session here — that engine is RLS-bound to a single tenant.
AdminSessionDep = Annotated["AsyncSession", Depends(get_admin_session)]
SuperuserDep = Annotated[User, Depends(current_superuser)]
UserManagerDep = Annotated["UserManager", Depends(get_user_manager)]


admin_router = APIRouter()


@admin_router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: AdminUserCreate,
    user_manager: UserManagerDep,
    _superuser: SuperuserDep,
) -> User:
    """Create a user. Operator-only (no self-signup in this milestone)."""
    user_create = UserCreate(
        email=payload.email,
        password=payload.password,
        is_active=payload.is_active,
        is_superuser=payload.is_superuser,
        is_verified=payload.is_verified,
    )
    try:
        return await user_manager.create(user_create, safe=False)
    except UserAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        ) from exc
    except InvalidPasswordException as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.reason,
        ) from exc


@admin_router.get("/users", response_model=list[UserRead])
async def list_users(
    session: AdminSessionDep,
    _superuser: SuperuserDep,
    limit: int = 50,
    offset: int = 0,
) -> list[User]:
    """List users in stable id order."""
    stmt = select(User).order_by(User.id).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars())


@admin_router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_user(
    user_id: uuid.UUID,
    session: AdminSessionDep,
    _superuser: SuperuserDep,
) -> None:
    """Delete a user. Cascades to memberships and access tokens.

    Uses a direct DELETE statement and relies on Postgres FK ``ON DELETE
    CASCADE`` to remove rows from ``org_memberships`` and ``access_tokens``,
    rather than SQLAlchemy ORM cascade — the ORM cascade triggers a lazy
    load of the relationship, which in async code requires explicit
    ``selectinload``/``passive_deletes`` to avoid greenlet errors. The
    server-side cascade is also faster and atomic.
    """
    stmt = delete(User).where(User.id == user_id).returning(User.id)
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()


@admin_router.post(
    "/users/{user_id}/memberships",
    response_model=MembershipRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_membership(
    user_id: uuid.UUID,
    payload: MembershipCreate,
    session: AdminSessionDep,
    _superuser: SuperuserDep,
) -> OrgMembership:
    """Add a user to an organisation with a role."""
    if await session.get(User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    if await session.get(Organisation, payload.org_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found.",
        )

    membership = OrgMembership(
        user_id=user_id,
        org_id=payload.org_id,
        role=payload.role.value,
    )
    session.add(membership)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this organisation.",
        ) from exc
    await session.refresh(membership)
    return membership


@admin_router.delete(
    "/users/{user_id}/memberships/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_membership(
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    session: AdminSessionDep,
    _superuser: SuperuserDep,
) -> None:
    """Remove a user's membership in an organisation."""
    stmt = (
        delete(OrgMembership)
        .where(OrgMembership.user_id == user_id, OrgMembership.org_id == org_id)
        .returning(OrgMembership.id)
    )
    result = await session.execute(stmt)
    deleted_id = result.scalar_one_or_none()
    if deleted_id is None:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
