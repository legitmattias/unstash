"""Document ingestion routes mounted under ``/api/orgs/{slug}/``."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Path, Query, UploadFile, status
from sqlalchemy import select

from unstash.config import get_settings
from unstash.db.models import Document, JobProgress
from unstash.documents.schemas import (
    DocumentRead,
    DocumentUploadResponse,
    JobProgressRead,
)
from unstash.documents.storage import (
    UploadTooLargeError,
    write_uploaded_file,
)
from unstash.orgs.dependencies import CurrentUserDep, OrgContextDep
from unstash.tasks import ingest_document

documents_router = APIRouter()


@documents_router.post(
    "/orgs/{slug}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    ctx: OrgContextDep,
    user: CurrentUserDep,
    file: Annotated[UploadFile, File(...)],
) -> DocumentUploadResponse:
    """Accept a file upload, store it on disk, queue ingestion.

    The file streams to ``{documents_root}/{org_id}/{document_id}/``
    with the original filename. A row is inserted in ``documents``
    with ``status='pending'`` and a matching row in ``job_progress``
    with ``status='queued'``. The ``ingest_document`` task is then
    queued; the task body sets the document to ``parsing`` and then
    ``parsed`` (Phase A stub — real parsing arrives in Phase B).

    Returns the new document id and job id immediately. The caller
    polls ``GET /api/orgs/{slug}/jobs/{id}`` to follow progress.
    """
    settings = get_settings()
    document_id = uuid.uuid4()
    try:
        stored = await write_uploaded_file(
            upload=file,
            documents_root=settings.documents_root,
            org_id=ctx.org_id,
            document_id=document_id,
            max_bytes=settings.max_upload_bytes,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc

    title = file.filename or stored.path.name
    mime_type = file.content_type or "application/octet-stream"

    document = Document(
        id=document_id,
        org_id=ctx.org_id,
        title=title,
        source_uri=str(stored.path),
        mime_type=mime_type,
        size_bytes=stored.size_bytes,
        content_hash=stored.sha256_hex,
        status="pending",
    )
    ctx.session.add(document)

    job = JobProgress(
        org_id=ctx.org_id,
        task_id="pending",  # overwritten after kiq below
        task_name="ingest_document",
        status="queued",
    )
    ctx.session.add(job)
    await ctx.session.flush()

    sent = await ingest_document.kiq(
        str(ctx.org_id),
        str(document_id),
        str(job.id),
    )
    job.task_id = sent.task_id

    _ = user  # explicit: present only to gate auth via dependency
    return DocumentUploadResponse(document_id=document.id, job_id=job.id)


@documents_router.get(
    "/orgs/{slug}/documents",
    response_model=list[DocumentRead],
)
async def list_documents(
    ctx: OrgContextDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Document]:
    """List documents in this org, newest first.

    RLS scopes the query to ``app.current_org_id`` automatically; the
    handler only orders and paginates.
    """
    _ = user
    stmt = select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
    return list((await ctx.session.execute(stmt)).scalars())


@documents_router.get(
    "/orgs/{slug}/documents/{document_id}",
    response_model=DocumentRead,
)
async def get_document(
    ctx: OrgContextDep,
    user: CurrentUserDep,
    document_id: Annotated[uuid.UUID, Path(...)],
) -> Document:
    """Return a single document by id, or 404 if not in this org."""
    _ = user
    document = await ctx.session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return document


@documents_router.get(
    "/orgs/{slug}/jobs/{job_id}",
    response_model=JobProgressRead,
)
async def get_job(
    ctx: OrgContextDep,
    user: CurrentUserDep,
    job_id: Annotated[uuid.UUID, Path(...)],
) -> JobProgress:
    """Return the current state of a background job."""
    _ = user
    job = await ctx.session.get(JobProgress, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return job
