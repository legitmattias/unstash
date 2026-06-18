"""Pydantic response models for the org-scoped routes."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from unstash.db.models.org_membership import OrgRole


class MembershipRead(BaseModel):
    """Membership record returned to clients."""

    id: uuid.UUID
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: OrgRole

    model_config = ConfigDict(from_attributes=True)
