"""SQLAlchemy ORM models.

Importing this module registers every model with ``Base.metadata`` as a
side effect, which is what Alembic's autogenerate machinery needs in
``alembic/env.py``. New models must be added to the imports below — if a
model isn't imported here, Alembic won't see it.
"""

from __future__ import annotations

from unstash.db.models.base import Base, TimestampMixin
from unstash.db.models.org_membership import OrgMembership, OrgRole
from unstash.db.models.organisation import Organisation
from unstash.db.models.user import User

__all__ = [
    "Base",
    "OrgMembership",
    "OrgRole",
    "Organisation",
    "TimestampMixin",
    "User",
]
