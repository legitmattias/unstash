"""Pydantic schemas for the admin endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from unstash.db.models.org_membership import OrgRole


class AdminUserCreate(BaseModel):
    """Operator-supplied payload for creating a user."""

    email: EmailStr
    password: str = Field(min_length=8)
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = True


class MembershipCreate(BaseModel):
    """Payload for adding a user to an organisation with a role."""

    org_id: uuid.UUID
    role: OrgRole


class MembershipRead(BaseModel):
    """Membership record returned to clients."""

    id: uuid.UUID
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: OrgRole

    model_config = ConfigDict(from_attributes=True)
