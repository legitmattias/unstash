"""Pydantic read/create/update schemas for the User model."""

from __future__ import annotations

import uuid

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    """User representation returned to clients (no password fields)."""


class UserCreate(schemas.BaseUserCreate):
    """Payload for creating a user (admin endpoint, PR 2)."""


class UserUpdate(schemas.BaseUserUpdate):
    """Payload for updating a user (admin endpoint, PR 2)."""
