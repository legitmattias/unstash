"""Clustering worker task.

Queued by the ingestion pipeline when the re-cluster trigger fires (see
:func:`unstash.clustering.service.pending_trigger`) and by the manual
re-cluster action. The task body delegates to the clustering service,
which manages its own org-scoped transactions.
"""

from __future__ import annotations

import uuid

from unstash.clustering.service import execute_clustering
from unstash.db.models import ClusteringTrigger
from unstash.tasks.broker import broker


@broker.task
async def cluster_org_documents(org_id_str: str, triggered_by: str) -> None:
    """Cluster an org's indexed documents and persist the assignments."""
    await execute_clustering(
        uuid.UUID(org_id_str),
        ClusteringTrigger(triggered_by),
    )
