"""Ingestion task — Phase B body.

The job:

1. Loads the document row and the matching job_progress row.
2. Sets document.status to ``parsing`` and job.status to ``running``.
3. Sniffs the file's MIME type, picks a parse strategy, and dispatches:

   - ``EXTRACT``: parse with Docling, run HybridChunker, insert chunks
     with NULL embedding (M3-C fills embeddings later). Sets
     ``document.status = 'parsed'``.
   - ``CONVERT_THEN_EXTRACT``: not wired in this PR (M3-B PR 2 adds
     Gotenberg). Currently treated as ``SKIP``.
   - ``METADATA_ONLY``: skip chunking; mark ``parsed`` (the document
     is recognised, just not chunkable). Phase D may add metadata
     extraction here.
   - ``SKIP``: mark ``failed`` with an explanation in
     ``document.parsing_error``.

4. On any uncaught exception during parsing, transitions the document
   to ``failed`` and captures the error in ``parsing_error``. The
   transaction is committed even for failure so the operator-visible
   state is consistent; the task itself surfaces the exception so
   Taskiq's retry/dead-letter logic can still apply.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select

from unstash.db.models import Chunk, Document, JobProgress
from unstash.tasks.broker import broker
from unstash.tasks.context import org_context

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from unstash.documents.parser import ParsedDocument

# Imports of unstash.documents.mime / parser / strategy are deferred
# into _run_parse so the API container does not pay the Docling +
# transformers + torch import cost. Only the worker process actually
# runs this task body, so only the worker loads those modules. This
# keeps the API container well within its memory limit (was OOM-killed
# at 384 MB when it transitively imported torch via the task module).

logger = structlog.get_logger(__name__)


@broker.task
async def ingest_document(
    org_id_str: str,
    document_id_str: str,
    job_id_str: str,
) -> None:
    """Drive a document through the M3-B parse lifecycle."""
    org_id = uuid.UUID(org_id_str)
    document_id = uuid.UUID(document_id_str)
    job_id = uuid.UUID(job_id_str)

    async with org_context(org_id) as session:
        document = await session.get(Document, document_id)
        job = await session.get(JobProgress, job_id)
        if document is None or job is None:
            logger.warning(
                "ingest_document_target_missing",
                org_id=str(org_id),
                document_id=str(document_id),
                job_id=str(job_id),
                document_present=document is not None,
                job_present=job is not None,
            )
            return

        started = datetime.now(UTC)
        document.status = "parsing"
        job.status = "running"
        job.started_at = started
        await session.flush()

        file_path = Path(document.source_uri)
        try:
            await _run_parse(session, document, file_path)
            document.status = _resolve_final_status(document)
        except Exception as exc:
            logger.info(
                "ingest_document_failed",
                org_id=str(org_id),
                document_id=str(document_id),
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )
            document.status = "failed"
            document.parsing_error = f"{type(exc).__name__}: {exc}"
            job.status = "failed"
            job.error = document.parsing_error
            job.finished_at = datetime.now(UTC)
            await session.flush()
            return

        job.status = "succeeded"
        job.finished_at = datetime.now(UTC)
        chunk_count = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.document_id == document.id),
        )
        logger.info(
            "ingest_document_completed",
            org_id=str(org_id),
            document_id=str(document_id),
            chunks=chunk_count,
        )


async def _run_parse(
    session: AsyncSession,
    document: Document,
    file_path: Path,
) -> None:
    """Dispatch by strategy, write chunks, update document fields.

    Sync MIME detection and parsing are wrapped in :func:`asyncio.to_thread`
    so the worker event loop is not blocked on disk reads or
    CPU-bound document parsing.

    The Docling-using imports below are deferred to function scope so
    the API container does not pay the model-stack import cost. Only
    the worker that actually runs this task loads them.
    """
    from unstash.documents.mime import detect_mime  # noqa: PLC0415
    from unstash.documents.parser import (  # noqa: PLC0415
        PIPELINE_VERSION,
        parse_to_chunks,
    )
    from unstash.documents.strategy import (  # noqa: PLC0415
        ParseStrategy,
        select_strategy,
    )

    mime = await asyncio.to_thread(detect_mime, file_path)
    strategy = select_strategy(mime)

    logger.info(
        "ingest_strategy_selected",
        document_id=str(document.id),
        declared_mime=document.mime_type,
        detected_mime=mime,
        strategy=strategy.value,
    )

    # Trust the sniffed MIME over the operator-declared one. Both are
    # surfaced via logging so a mismatch is visible during incident
    # triage even though it does not block parsing.
    document.mime_type = mime

    if strategy is ParseStrategy.EXTRACT:
        logger.info("ingest_parse_starting", document_id=str(document.id))
        try:
            parsed: ParsedDocument = await asyncio.to_thread(parse_to_chunks, file_path)
        except BaseException as exc:
            logger.error(
                "ingest_parse_raised",
                document_id=str(document.id),
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )
            raise
        logger.info(
            "ingest_parse_finished",
            document_id=str(document.id),
            chunks=len(parsed.chunks),
        )
        for parsed_chunk in parsed.chunks:
            session.add(
                Chunk(
                    org_id=document.org_id,
                    document_id=document.id,
                    chunk_index=parsed_chunk.chunk_index,
                    text=parsed_chunk.text,
                    token_count=parsed_chunk.token_count,
                    char_offset_start=parsed_chunk.char_offset_start,
                    char_offset_end=parsed_chunk.char_offset_end,
                ),
            )
        document.pipeline_version = parsed.pipeline_version
        document.pipeline_config = parsed.pipeline_config
        return

    if strategy is ParseStrategy.METADATA_ONLY:
        # Recognised but not chunkable in this phase. Record provenance
        # so a later metadata-extraction phase knows this document was
        # seen by the M3-B pipeline.
        document.pipeline_version = f"{PIPELINE_VERSION} (metadata_only)"
        document.pipeline_config = {"strategy": "metadata_only", "detected_mime": mime}
        return

    if strategy is ParseStrategy.CONVERT_THEN_EXTRACT:
        # Gotenberg sidecar lands in M3-B PR 2. Until then we treat
        # legacy office formats as "not yet supported" so they fail
        # loudly rather than silently producing zero chunks.
        msg = f"Legacy office format not yet supported: {mime}"
        raise NotImplementedError(msg)

    # Unsupported or actively suspicious. Fail with an actionable
    # parsing_error so the operator can see why and either re-upload
    # in a supported format or escalate.
    msg = f"Unsupported MIME type: {mime}"
    raise ValueError(msg)


def _resolve_final_status(document: Document) -> str:
    """Pick the right post-parse status for the document."""
    # A successful EXTRACT or METADATA_ONLY both land on ``parsed``.
    # The transition to ``indexed`` happens later (M3-C, after
    # embeddings are written).
    return "parsed"
