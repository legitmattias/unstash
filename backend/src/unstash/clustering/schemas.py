"""Response bodies for the clusters endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ClusterKeyword(BaseModel):
    """One c-TF-IDF term with its score."""

    term: str
    score: float


class ClusterSummary(BaseModel):
    """One discovered document category."""

    id: uuid.UUID
    label: str
    label_source: str
    size: int
    keywords: list[ClusterKeyword]
    representative_document_ids: list[uuid.UUID]


class ClustersResponse(BaseModel):
    """Clusters of the latest succeeded run, or empty when never clustered."""

    run_id: uuid.UUID | None
    run_created_at: datetime | None
    document_count: int | None
    noise_count: int | None
    silhouette: float | None
    clusters: list[ClusterSummary]


class ReclusterResponse(BaseModel):
    """Acknowledgement of an enqueued manual clustering run."""

    enqueued: bool
