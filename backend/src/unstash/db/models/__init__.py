"""SQLAlchemy ORM models.

Importing this module registers every model with ``Base.metadata`` as a
side effect, which is what Alembic's autogenerate machinery needs in
``alembic/env.py``. New models must be added to the imports below — if a
model isn't imported here, Alembic won't see it.
"""

from __future__ import annotations

from unstash.db.models.audit_log import AuditLog
from unstash.db.models.base import Base, TimestampMixin
from unstash.db.models.chunk import Chunk
from unstash.db.models.connector import Connector, ConnectorProvider, ConnectorStatus
from unstash.db.models.document import Document, DocumentStatus
from unstash.db.models.job_progress import JobProgress, JobStatus
from unstash.db.models.org_membership import OrgMembership, OrgRole
from unstash.db.models.organisation import Organisation
from unstash.db.models.search_log import SearchLog
from unstash.db.models.user import User

__all__ = [
    "AuditLog",
    "Base",
    "Chunk",
    "Connector",
    "ConnectorProvider",
    "ConnectorStatus",
    "Document",
    "DocumentStatus",
    "JobProgress",
    "JobStatus",
    "OrgMembership",
    "OrgRole",
    "Organisation",
    "SearchLog",
    "TimestampMixin",
    "User",
]
