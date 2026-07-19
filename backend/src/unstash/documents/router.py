"""Document ingestion routes mounted under ``/api/orgs/{slug}/``."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime, time
from typing import Annotated

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select

from unstash.config import get_settings
from unstash.db.models import Document, DocumentStatus, JobProgress, JobStatus, Organisation
from unstash.documents.schemas import (
    DocumentRead,
    DocumentUploadResponse,
    JobProgressRead,
)
from unstash.documents.storage import (
    UploadTooLargeError,
    write_uploaded_file,
)
from unstash.orgs.dependencies import OrgContext, OrgContextDep
from unstash.tasks import ingest_document

documents_router = APIRouter()


async def _enforce_daily_upload_limit(ctx: OrgContext) -> None:
    """Reject the upload with 429 once the org's daily limit is reached.

    The limit counts document rows created since UTC midnight, so failed
    ingestions count (the limit is abuse protection, not a success quota)
    while deduplicated uploads do not (they create no row). Runs before
    the file write so a rejected upload never touches disk.
    """
    org = await ctx.session.get(Organisation, ctx.org_id)
    limit = org.daily_upload_limit if org is not None else None
    if limit is None:
        return

    day_start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    uploads_today = await ctx.session.scalar(
        select(func.count(Document.id)).where(Document.created_at >= day_start),
    )
    if (uploads_today or 0) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily upload limit reached ({limit} per day).",
        )


@documents_router.post(
    "/orgs/{slug}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    ctx: OrgContextDep,
    file: Annotated[UploadFile, File(...)],
    response: Response,
) -> DocumentUploadResponse:
    """Accept a file upload, store it on disk, queue ingestion.

    The file streams to ``{documents_root}/{org_id}/{document_id}/``
    with the original filename. A row is inserted in ``documents``
    with ``status='pending'`` and a matching row in ``job_progress``
    with ``status='queued'``. The ``ingest_document`` task is then
    queued; the task body detects the MIME type, routes it through the
    parse strategy, and moves the document to ``parsed`` or ``failed``.

    Duplicate uploads (same SHA-256 already in this org, unless that
    document ``failed``) are not re-ingested: the stored file is removed
    and the existing document id is returned with ``duplicate=true`` and
    HTTP 200.

    Returns the new document id and job id immediately. The caller
    polls ``GET /api/orgs/{slug}/jobs/{id}`` to follow progress.
    """
    await _enforce_daily_upload_limit(ctx)

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

    # Dedup by content hash within the org (RLS scopes the query).
    # A previously failed document does not block a retry upload.
    existing = (
        await ctx.session.execute(
            select(Document)
            .where(
                Document.content_hash == stored.sha256_hex,
                Document.status != DocumentStatus.FAILED,
            )
            .limit(1),
        )
    ).scalar_one_or_none()
    if existing is not None:
        await asyncio.to_thread(shutil.rmtree, stored.path.parent, True)
        response.status_code = status.HTTP_200_OK
        return DocumentUploadResponse(
            document_id=existing.id,
            job_id=None,
            duplicate=True,
        )

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
        status=DocumentStatus.PENDING,
    )
    ctx.session.add(document)

    job = JobProgress(
        org_id=ctx.org_id,
        task_id="pending",  # overwritten after kiq below
        task_name="ingest_document",
        status=JobStatus.QUEUED,
    )
    ctx.session.add(job)
    await ctx.session.flush()

    sent = await ingest_document.kiq(
        str(ctx.org_id),
        str(document_id),
        str(job.id),
    )
    job.task_id = sent.task_id

    return DocumentUploadResponse(document_id=document.id, job_id=job.id)


@documents_router.get(
    "/orgs/{slug}/documents",
    response_model=list[DocumentRead],
)
async def list_documents(
    ctx: OrgContextDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Document]:
    """List documents in this org, newest first.

    RLS scopes the query to ``app.current_org_id`` automatically; the
    handler only orders and paginates.
    """
    stmt = select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
    return list((await ctx.session.execute(stmt)).scalars())


@documents_router.get(
    "/orgs/{slug}/documents/{document_id}",
    response_model=DocumentRead,
)
async def get_document(
    ctx: OrgContextDep,
    document_id: Annotated[uuid.UUID, Path(...)],
) -> Document:
    """Return a single document by id, or 404 if not in this org."""
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
    job_id: Annotated[uuid.UUID, Path(...)],
) -> JobProgress:
    """Return the current state of a background job."""
    job = await ctx.session.get(JobProgress, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return job
