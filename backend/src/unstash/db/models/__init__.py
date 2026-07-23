"""SQLAlchemy ORM models.

Importing this module registers every model with ``Base.metadata`` as a
side effect, which is what Alembic's autogenerate machinery needs in
``alembic/env.py``. New models must be added to the imports below — if a
model isn't imported here, Alembic won't see it.
"""

from __future__ import annotations

from unstash.db.models.access_token import AccessToken
from unstash.db.models.api_token import ApiToken
from unstash.db.models.audit_log import AuditLog
from unstash.db.models.base import Base, TimestampMixin
from unstash.db.models.chunk import Chunk
from unstash.db.models.cluster import Cluster, ClusterLabelSource
from unstash.db.models.clustering_run import (
    ClusteringRun,
    ClusteringRunStatus,
    ClusteringTrigger,
)
from unstash.db.models.connector import Connector, ConnectorProvider, ConnectorStatus
from unstash.db.models.document import Document, DocumentStatus
from unstash.db.models.document_metadata import DocumentMetadata
from unstash.db.models.job_progress import JobProgress, JobStatus
from unstash.db.models.org_membership import OrgMembership, OrgRole
from unstash.db.models.organisation import Organisation
from unstash.db.models.search_log import SearchLog
from unstash.db.models.user import User

__all__ = [
    "AccessToken",
    "ApiToken",
    "AuditLog",
    "Base",
    "Chunk",
    "Cluster",
    "ClusterLabelSource",
    "ClusteringRun",
    "ClusteringRunStatus",
    "ClusteringTrigger",
    "Connector",
    "ConnectorProvider",
    "ConnectorStatus",
    "Document",
    "DocumentMetadata",
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
