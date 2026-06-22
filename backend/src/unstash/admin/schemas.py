"""Pydantic schemas for the admin endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

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


class ApiTokenCreate(BaseModel):
    """Payload for creating an API token on behalf of a user."""

    name: str = Field(min_length=1, max_length=200)
    org_id: uuid.UUID | None = None
    expires_at: datetime | None = None


class ApiTokenRead(BaseModel):
    """Token metadata (no plaintext, no hash)."""

    id: uuid.UUID
    user_id: uuid.UUID
    org_id: uuid.UUID | None
    name: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ApiTokenCreated(ApiTokenRead):
    """Token metadata plus the plaintext value.

    Returned only at creation — the plaintext is never persisted, so
    this is the operator's single chance to capture it.
    """

    token: str
