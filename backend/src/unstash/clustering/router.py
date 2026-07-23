"""Clusters endpoints — category listing and the manual re-cluster action.

The listing serves both the search sidebar (id + label for the category
filter) and the admin cluster overview (keywords, representatives, run
quality signals). Cluster rows are org-scoped under RLS; the org context
dependency both authorises membership and scopes every query.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from unstash.clustering.schemas import (
    ClusterKeyword,
    ClustersResponse,
    ClusterSummary,
    ReclusterResponse,
)
from unstash.db.models import (
    Cluster,
    ClusteringRun,
    ClusteringRunStatus,
    ClusteringTrigger,
)
from unstash.orgs.dependencies import OrgContextDep

clusters_router = APIRouter()


@clusters_router.get("/orgs/{slug}/clusters", response_model=ClustersResponse)
async def list_clusters(ctx: OrgContextDep) -> ClustersResponse:
    """Return the clusters of the latest succeeded run."""
    run = (
        await ctx.session.execute(
            select(ClusteringRun)
            .where(
                ClusteringRun.org_id == ctx.org_id,
                ClusteringRun.status == ClusteringRunStatus.SUCCEEDED,
            )
            .order_by(ClusteringRun.created_at.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    if run is None:
        return ClustersResponse(
            run_id=None,
            run_created_at=None,
            document_count=None,
            noise_count=None,
            silhouette=None,
            clusters=[],
        )

    clusters = (
        (
            await ctx.session.execute(
                select(Cluster)
                .where(Cluster.org_id == ctx.org_id, Cluster.run_id == run.id)
                .order_by(Cluster.size.desc(), Cluster.id),
            )
        )
        .scalars()
        .all()
    )
    return ClustersResponse(
        run_id=run.id,
        run_created_at=run.created_at,
        document_count=run.document_count,
        noise_count=run.noise_count,
        silhouette=run.silhouette,
        clusters=[
            ClusterSummary(
                id=cluster.id,
                label=cluster.label,
                label_source=cluster.label_source,
                size=cluster.size,
                keywords=[
                    ClusterKeyword(term=str(k["term"]), score=float(k["score"]))
                    for k in cluster.keywords
                ],
                representative_document_ids=[
                    uuid.UUID(doc_id) for doc_id in cluster.representative_document_ids
                ],
            )
            for cluster in clusters
        ],
    )


@clusters_router.post(
    "/orgs/{slug}/clusters/recluster",
    response_model=ReclusterResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def recluster(ctx: OrgContextDep) -> ReclusterResponse:
    """Enqueue a manual clustering run for the org."""
    from unstash.tasks.cluster import cluster_org_documents  # noqa: PLC0415

    in_flight = await ctx.session.scalar(
        select(func.count())
        .select_from(ClusteringRun)
        .where(
            ClusteringRun.org_id == ctx.org_id,
            ClusteringRun.status == ClusteringRunStatus.RUNNING,
        ),
    )
    if in_flight:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A clustering run is already in progress.",
        )

    await cluster_org_documents.kiq(str(ctx.org_id), ClusteringTrigger.MANUAL.value)
    return ReclusterResponse(enqueued=True)
