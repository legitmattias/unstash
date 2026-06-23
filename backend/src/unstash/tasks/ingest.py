"""Ingestion task — Phase A stub.

This is the placeholder body that PR 2 of M3-A ships so the
end-to-end plumbing can be exercised. The job:

1. Marks the document as ``parsing``.
2. (Phase B will do real parsing here.)
3. Marks the document as ``parsed``.
4. Walks the matching ``job_progress`` row through
   ``queued → running → succeeded``.

The status transitions and timestamps are the contract the
monitoring routes surface to the UI. Phase B will replace the
no-op body between ``parsing`` and ``parsed`` with the actual
parse-and-chunk implementation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from unstash.db.models import Document, JobProgress
from unstash.tasks.broker import broker
from unstash.tasks.context import org_context

logger = structlog.get_logger(__name__)


@broker.task
async def ingest_document(
    org_id_str: str,
    document_id_str: str,
    job_id_str: str,
) -> None:
    """Drive a document through the Phase A no-op lifecycle.

    Args are passed as strings because Taskiq's default JSON
    serialisation does not handle :class:`uuid.UUID` natively.
    """
    org_id = uuid.UUID(org_id_str)
    document_id = uuid.UUID(document_id_str)
    job_id = uuid.UUID(job_id_str)

    async with org_context(org_id) as session:
        document = await session.get(Document, document_id)
        job = await session.get(JobProgress, job_id)
        if document is None or job is None:
            # The route inserted both rows inside the same transaction
            # as it queued the task. If we cannot see them here, that
            # transaction rolled back after the queue write — the task
            # is for state that was never committed. Log and exit.
            logger.warning(
                "ingest_document_target_missing",
                org_id=str(org_id),
                document_id=str(document_id),
                job_id=str(job_id),
                document_present=document is not None,
                job_present=job is not None,
            )
            return

        now = datetime.now(UTC)
        document.status = "parsing"
        job.status = "running"
        job.started_at = now
        await session.flush()

        # Phase B will fill in actual parsing here. For now the
        # transition pending → parsing → parsed is just bookkeeping.

        finished = datetime.now(UTC)
        document.status = "parsed"
        job.status = "succeeded"
        job.finished_at = finished

        logger.info(
            "ingest_document_completed_stub",
            org_id=str(org_id),
            document_id=str(document_id),
            job_id=str(job_id),
        )
