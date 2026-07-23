"""Ingestion tasks — parse, then embed.

Ingest runs as two chained Taskiq tasks so a crash in one half does not
redo the other:

``ingest_document`` (parse): sniffs the MIME type, routes via the
strategy router, and produces chunks with NULL embedding — EXTRACT via
Docling, CONVERT_THEN_EXTRACT via Gotenberg then Docling, METADATA_ONLY
records provenance only, SKIP fails with a reason. On success it commits,
sets the document to ``parsed``, and queues ``embed_document``.

``embed_document`` (embed + index): embeds the document's NULL-embedding
chunks and moves it to ``indexed``. Idempotent — a retry finishes the
remaining chunks without re-parsing.

Either task, on an uncaught exception, transitions the document to
``failed`` with the reason in ``parsing_error`` and commits so the
operator-visible state stays consistent.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from unstash.db.models import (
    Chunk,
    Document,
    DocumentMetadata,
    DocumentStatus,
    JobProgress,
    JobStatus,
)
from unstash.documents.metadata import (
    EXTRACTOR_VERSION,
    extract_amounts,
    extract_dates,
)
from unstash.tasks.broker import broker
from unstash.tasks.context import org_context
from unstash.tasks.instrumentation import peak_rss_mib, queue_depth

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from unstash.documents.parser import ParsedDocument

# Parser imports are deferred into _run_parse so only the worker
# process (which runs the task body) loads Docling + transformers +
# torch. The API process imports this module to queue tasks but
# never executes the body.

logger = structlog.get_logger(__name__)

# Chunks per Jina request; tune against observed latency/cost.
_EMBED_BATCH_SIZE = 32

# The producer queues a task inside its own transaction, so the task can
# start before the rows it targets are committed. Retries bridge that
# window; a producer rollback means the rows never appear and we give up.
_TARGET_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)


async def _fail_job(  # noqa: PLR0913 — the failure transition needs all of these; bundling buys nothing
    session: AsyncSession,
    document: Document,
    job: JobProgress,
    exc: Exception,
    started: datetime,
    *,
    event: str,
    extra: dict[str, object] | None = None,
) -> None:
    """Transition a document and its job to the failed state, and log it."""
    finished = datetime.now(UTC)
    logger.info(
        event,
        org_id=str(document.org_id),
        document_id=str(document.id),
        exc_type=type(exc).__name__,
        exc_msg=str(exc),
        duration_ms=round((finished - started).total_seconds() * 1000),
        **(extra or {}),
    )
    document.status = DocumentStatus.FAILED
    document.parsing_error = f"{type(exc).__name__}: {exc}"
    job.status = JobStatus.FAILED
    job.error = document.parsing_error
    job.finished_at = finished
    await session.flush()


async def _load_targets(
    session: AsyncSession,
    document_id: uuid.UUID,
    job_id: uuid.UUID,
) -> tuple[Document | None, JobProgress | None]:
    """Load the task's document + job rows, tolerating producer-commit lag."""
    document = None
    job = None
    for delay in _TARGET_RETRY_DELAYS:
        document = await session.get(Document, document_id)
        job = await session.get(JobProgress, job_id)
        if document is not None and job is not None:
            break
        await asyncio.sleep(delay)
    else:
        document = await session.get(Document, document_id)
        job = await session.get(JobProgress, job_id)
    return document, job


@broker.task
async def ingest_document(
    org_id_str: str,
    document_id_str: str,
    job_id_str: str,
) -> None:
    """Parse a document into chunks, then queue embedding.

    On a successful parse the transaction commits and ``embed_document``
    is queued afterwards, so the embed task never races ahead of the
    chunks it reads.
    """
    org_id = uuid.UUID(org_id_str)
    document_id = uuid.UUID(document_id_str)
    job_id = uuid.UUID(job_id_str)

    parsed_ok = False
    async with org_context(org_id) as session:
        document, job = await _load_targets(session, document_id, job_id)
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
        document.status = DocumentStatus.PARSING
        job.status = JobStatus.RUNNING
        job.started_at = started
        await session.flush()

        logger.info(
            "ingest_job_started",
            org_id=str(org_id),
            document_id=str(document_id),
            queue_depth=await queue_depth(broker),
        )

        file_path = Path(document.source_uri)
        try:
            await _run_parse(session, document, file_path)
        except Exception as exc:
            await _fail_job(
                session,
                document,
                job,
                exc,
                started,
                event="ingest_document_failed",
                extra={"peak_rss_mib": round(peak_rss_mib(), 1)},
            )
            return

        document.status = DocumentStatus.PARSED
        chunk_count = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.document_id == document.id),
        )
        logger.info(
            "ingest_parse_completed",
            org_id=str(org_id),
            document_id=str(document_id),
            chunks=chunk_count,
            duration_ms=round((datetime.now(UTC) - started).total_seconds() * 1000),
            peak_rss_mib=round(peak_rss_mib(), 1),
        )
        parsed_ok = True

    if parsed_ok:
        await embed_document.kiq(org_id_str, document_id_str, job_id_str)


@broker.task
async def embed_document(
    org_id_str: str,
    document_id_str: str,
    job_id_str: str,
) -> None:
    """Embed a parsed document's chunks and mark it indexed.

    Idempotent: only chunks with a NULL embedding are embedded, so a
    retry after a mid-embed crash finishes the remaining chunks without
    re-parsing.
    """
    from unstash.clustering.service import pending_trigger  # noqa: PLC0415
    from unstash.config import get_settings  # noqa: PLC0415
    from unstash.documents.embedder import (  # noqa: PLC0415
        EmbeddingTask,
        get_embedder,
    )
    from unstash.tasks.cluster import cluster_org_documents  # noqa: PLC0415

    org_id = uuid.UUID(org_id_str)
    document_id = uuid.UUID(document_id_str)
    job_id = uuid.UUID(job_id_str)

    trigger = None
    async with org_context(org_id) as session:
        document, job = await _load_targets(session, document_id, job_id)
        if document is None or job is None:
            logger.warning(
                "embed_document_target_missing",
                org_id=str(org_id),
                document_id=str(document_id),
                job_id=str(job_id),
                document_present=document is not None,
                job_present=job is not None,
            )
            return

        started = datetime.now(UTC)
        result = await session.execute(
            select(Chunk)
            .where(Chunk.document_id == document.id, Chunk.embedding.is_(None))
            .order_by(Chunk.chunk_index),
        )
        pending = list(result.scalars())

        embedder = get_embedder(get_settings())
        total_tokens = 0
        try:
            for start in range(0, len(pending), _EMBED_BATCH_SIZE):
                batch = pending[start : start + _EMBED_BATCH_SIZE]
                embedded = await embedder.embed(
                    [chunk.text for chunk in batch],
                    task=EmbeddingTask.PASSAGE,
                )
                for chunk, vector in zip(batch, embedded.vectors, strict=True):
                    chunk.embedding = vector
                total_tokens += embedded.total_tokens
        except Exception as exc:
            await _fail_job(
                session,
                document,
                job,
                exc,
                started,
                event="embed_document_failed",
            )
            return

        finished = datetime.now(UTC)
        document.status = DocumentStatus.INDEXED
        document.indexed_at = finished
        job.status = JobStatus.SUCCEEDED
        job.finished_at = finished
        logger.info(
            "embed_document_completed",
            org_id=str(org_id),
            document_id=str(document_id),
            embedded_chunks=len(pending),
            total_tokens=total_tokens,
            duration_ms=round((finished - started).total_seconds() * 1000),
            peak_rss_mib=round(peak_rss_mib(), 1),
        )

        trigger = await pending_trigger(session, org_id)

    # Enqueued after the transaction commits so the clustering task's own
    # document count includes this document.
    if trigger is not None:
        await cluster_org_documents.kiq(str(org_id), trigger.value)
        logger.info(
            "clustering_enqueued",
            org_id=str(org_id),
            trigger=trigger.value,
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

    Parser imports are function-scoped so only the worker pays the
    Docling + transformers + torch load cost.
    """
    from unstash.documents.mime import detect_mime  # noqa: PLC0415
    from unstash.documents.parser import PIPELINE_VERSION  # noqa: PLC0415
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
        parsed = await _docling_parse(document, file_path)
        parsed = await _ocr_if_scanned(document, file_path, parsed, mime)
        _persist_chunks(session, document, parsed)
        document.pipeline_version = parsed.pipeline_version
        document.pipeline_config = parsed.pipeline_config
        await _extract_metadata(session, document, parsed)
        return

    if strategy is ParseStrategy.CONVERT_THEN_EXTRACT:
        from unstash.config import get_settings  # noqa: PLC0415
        from unstash.documents.conversion import convert_to_pdf  # noqa: PLC0415

        settings = get_settings()
        logger.info(
            "ingest_conversion_starting",
            document_id=str(document.id),
            detected_mime=mime,
        )
        pdf_bytes = await convert_to_pdf(
            file_path,
            gotenberg_url=settings.gotenberg_url,
            max_bytes=settings.external_processing_max_bytes,
            timeout=settings.gotenberg_timeout_seconds,
        )
        # Keep the converted PDF beside the original so a future
        # re-parse doesn't have to reconvert.
        converted_path = file_path.parent / "converted.pdf"
        await asyncio.to_thread(converted_path.write_bytes, pdf_bytes)
        logger.info(
            "ingest_conversion_finished",
            document_id=str(document.id),
            pdf_bytes=len(pdf_bytes),
        )

        parsed = await _docling_parse(document, converted_path)
        _persist_chunks(session, document, parsed)
        document.pipeline_version = f"{parsed.pipeline_version} (converted via gotenberg)"
        document.pipeline_config = {
            **parsed.pipeline_config,
            "converted_via": "gotenberg",
            "source_mime": mime,
        }
        await _extract_metadata(session, document, parsed)
        return

    if strategy is ParseStrategy.METADATA_ONLY:
        # Recognised but not chunkable. Record provenance so later
        # metadata extraction knows this document was seen by the
        # parse pipeline.
        document.pipeline_version = f"{PIPELINE_VERSION} (metadata_only)"
        document.pipeline_config = {"strategy": "metadata_only", "detected_mime": mime}
        return

    # Unsupported or actively suspicious. Fail with an actionable
    # parsing_error so the operator can see why and either re-upload
    # in a supported format or escalate.
    msg = f"Unsupported MIME type: {mime}"
    raise ValueError(msg)


async def _ocr_if_scanned(
    document: Document,
    file_path: Path,
    parsed: ParsedDocument,
    mime: str,
) -> ParsedDocument:
    """Route a low-text-density PDF through OCR, or fail actionably.

    A scanned PDF parses to (near-)zero text; without OCR it would index
    as silently empty, so with the backend off it fails loudly instead.
    The OCR markdown is kept beside the original and parsed through the
    normal path.
    """
    from unstash.config import get_settings  # noqa: PLC0415
    from unstash.documents.ocr import needs_ocr, ocr_pdf_to_markdown  # noqa: PLC0415

    if mime != "application/pdf":
        return parsed
    settings = get_settings()
    if not needs_ocr(
        page_count=parsed.page_count,
        total_chars=parsed.total_chars,
        min_chars_per_page=settings.ocr_min_chars_per_page,
    ):
        return parsed
    if settings.ocr_backend != "mistral":
        msg = (
            f"Scanned PDF: {parsed.total_chars} extracted chars over "
            f"{parsed.page_count} pages and OCR is disabled"
        )
        raise ValueError(msg)

    logger.info(
        "ingest_ocr_starting",
        document_id=str(document.id),
        pages=parsed.page_count,
        extracted_chars=parsed.total_chars,
    )
    markdown = await ocr_pdf_to_markdown(
        file_path,
        api_key=settings.mistral_api_key,
        base_url=settings.mistral_base_url,
        model=settings.mistral_ocr_model,
        max_bytes=settings.external_processing_max_bytes,
        timeout=settings.mistral_timeout_seconds,
    )
    # Keep the OCR text beside the original so re-parsing doesn't re-OCR.
    ocr_path = file_path.parent / "ocr.md"
    await asyncio.to_thread(ocr_path.write_text, markdown, "utf-8")

    ocr_parsed = await _docling_parse(document, ocr_path)
    logger.info(
        "ingest_ocr_finished",
        document_id=str(document.id),
        chunks=len(ocr_parsed.chunks),
        markdown_chars=len(markdown),
    )
    return replace(
        ocr_parsed,
        pipeline_version=f"{ocr_parsed.pipeline_version} (ocr via mistral)",
        pipeline_config={
            **ocr_parsed.pipeline_config,
            "ocr_via": "mistral",
            "ocr_model": settings.mistral_ocr_model,
            "source_pages": parsed.page_count,
        },
    )


async def _docling_parse(document: Document, path: Path) -> ParsedDocument:
    """Run Docling over a file path, off the event loop, with logging.

    Shared by the ``EXTRACT`` path (native PDF, text, and OOXML) and the
    ``CONVERT_THEN_EXTRACT`` path (legacy office already turned into a
    PDF by Gotenberg). The parser import is function-scoped so only the
    worker pays the Docling + transformers + torch load cost.
    """
    from unstash.documents.parser import parse_to_chunks  # noqa: PLC0415

    logger.info("ingest_parse_starting", document_id=str(document.id))
    try:
        parsed = await asyncio.to_thread(parse_to_chunks, path)
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
    return parsed


async def _extract_metadata(
    session: AsyncSession,
    document: Document,
    parsed: ParsedDocument,
) -> None:
    """Extract dates, amounts, and entities into document_metadata, best-effort.

    Failures log a warning and never fail the document — metadata is a
    search-filter enhancement, not part of the parse contract. Entity
    extraction (NER) runs only when a backend is configured
    (``ner_backend`` != 'off'); otherwise ``entities`` stays NULL.
    Upserts on document_id so a task retry refreshes rather than duplicates.
    """
    from unstash.config import get_settings  # noqa: PLC0415
    from unstash.documents.ner import get_entity_extractor  # noqa: PLC0415

    settings = get_settings()
    try:
        text = "\n".join(chunk.text for chunk in parsed.chunks)
        dates = [asdict(date) for date in extract_dates(text)]
        amounts = [asdict(amount) for amount in extract_amounts(text)]
        values: dict[str, object] = {
            "dates": dates,
            "amounts": amounts,
            "extractor_version": EXTRACTOR_VERSION,
        }
        # Only touch ``entities`` when a NER backend is configured, so a
        # NULL keeps meaning "never processed" — the signal the backfill
        # task selects on.
        entity_count: int | None = None
        if settings.ner_backend != "off":
            entities = [
                {"text": e.text, "label": e.label, "score": e.score}
                for e in await get_entity_extractor(settings).extract(text)
            ]
            values["entities"] = entities
            entity_count = len(entities)
        statement = (
            pg_insert(DocumentMetadata)
            .values(org_id=document.org_id, document_id=document.id, **values)
            .on_conflict_do_update(
                index_elements=["document_id"],
                set_={**values, "updated_at": func.now()},
            )
        )
        await session.execute(statement)
        logger.info(
            "metadata_extracted",
            document_id=str(document.id),
            dates=len(dates),
            amounts=len(amounts),
            entities=entity_count,
        )
    except Exception as exc:
        logger.warning(
            "metadata_extraction_failed",
            document_id=str(document.id),
            exc_type=type(exc).__name__,
            exc_msg=str(exc),
        )


def _persist_chunks(
    session: AsyncSession,
    document: Document,
    parsed: ParsedDocument,
) -> None:
    """Insert the parsed chunks for a document (embeddings stay NULL)."""
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
