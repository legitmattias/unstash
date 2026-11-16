"""Queue ingestion work that was lost or invalidated.

    python -m unstash.maintenance.requeue embeddings [--dry-run]
    python -m unstash.maintenance.requeue pending [--dry-run]

``embeddings`` queues the embed task for every parsed document that has
chunks without an embedding. Needed after a change of embedding model or
vector width, which leaves every document ``parsed`` with NULL
embeddings, and after an embed task was lost.

``pending`` queues the ingest task for every document still ``pending``.
A parse that dies with its worker commits nothing, so the document stays
``pending`` and its task is gone. The command cannot tell such a
document from one whose task is still waiting in the queue: run it only
when the queue is empty, or documents are parsed twice and the second
parse fails on the first one's chunks.

Both work across organisations, so they use the admin database role and
must run where that credential and the task broker are both available.
Each queued task runs in the worker under its own organisation's
context, exactly as after an upload. Each gets a new job row; the job of
a lost task is left as it was.

Repeating ``embeddings`` is safe: the embed task only fills chunks whose
embedding is still NULL.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import exists, select, update

from unstash.db.engine import dispose_engine
from unstash.db.models import Chunk, Document, DocumentStatus, JobProgress, JobStatus
from unstash.db.session import get_admin_sessionmaker

if TYPE_CHECKING:
    from typing import Any

    from sqlalchemy import Select
    from taskiq import AsyncTaskiqDecoratedTask

Target = tuple[uuid.UUID, uuid.UUID]


def _unembedded() -> Select[tuple[uuid.UUID, uuid.UUID]]:
    return (
        select(Document.id, Document.org_id)
        .where(
            Document.status == DocumentStatus.PARSED,
            exists().where(
                Chunk.document_id == Document.id,
                Chunk.embedding.is_(None),
            ),
        )
        .order_by(Document.created_at)
    )


def _pending() -> Select[tuple[uuid.UUID, uuid.UUID]]:
    return (
        select(Document.id, Document.org_id)
        .where(Document.status == DocumentStatus.PENDING)
        .order_by(Document.created_at)
    )


async def _requeue(
    statement: Select[tuple[uuid.UUID, uuid.UUID]],
    task: AsyncTaskiqDecoratedTask[Any, Any],
    *,
    dry_run: bool,
) -> int:
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        # Deliberately not filtered by org_id: a maintenance sweep over
        # every organisation, on the admin role.
        result = await session.execute(statement)
        targets: list[Target] = [(row[0], row[1]) for row in result]
        if dry_run or not targets:
            return len(targets)

        jobs: list[tuple[uuid.UUID, uuid.UUID, JobProgress]] = []
        for document_id, org_id in targets:
            job = JobProgress(
                org_id=org_id,
                # Unique placeholder, replaced with the broker's id below.
                task_id=f"requeue-{uuid.uuid4()}",
                task_name=task.task_name.rsplit(":", 1)[-1],
                status=JobStatus.QUEUED,
            )
            session.add(job)
            jobs.append((document_id, org_id, job))
        # Committed before sending so the worker finds its job row.
        await session.commit()

        for document_id, org_id, job in jobs:
            sent = await task.kiq(str(org_id), str(document_id), str(job.id))
            await session.execute(
                update(JobProgress).where(JobProgress.id == job.id).values(task_id=sent.task_id),
            )
        await session.commit()
        return len(targets)


async def requeue_embeddings(*, dry_run: bool) -> int:
    """Queue an embed task per parsed document with unembedded chunks."""
    # Imported here: importing the tasks package builds the broker.
    from unstash.tasks.ingest import embed_document  # noqa: PLC0415

    return await _requeue(_unembedded(), embed_document, dry_run=dry_run)


async def requeue_pending(*, dry_run: bool) -> int:
    """Queue an ingest task per pending document."""
    from unstash.tasks.ingest import ingest_document  # noqa: PLC0415

    return await _requeue(_pending(), ingest_document, dry_run=dry_run)


async def _run(what: str, *, dry_run: bool) -> int:
    from unstash.tasks import broker  # noqa: PLC0415

    await broker.startup()
    try:
        if what == "pending":
            return await requeue_pending(dry_run=dry_run)
        return await requeue_embeddings(dry_run=dry_run)
    finally:
        await broker.shutdown()
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m unstash.maintenance.requeue",
        description="Queue ingestion work that was lost or invalidated.",
    )
    parser.add_argument(
        "what",
        choices=("embeddings", "pending"),
        help=(
            "embeddings: parsed documents with chunks lacking an embedding. "
            "pending: documents never parsed; only when the task queue is empty."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many documents would be queued, without queueing them.",
    )
    args = parser.parse_args(argv)
    count = asyncio.run(_run(args.what, dry_run=args.dry_run))
    verb = "would be queued" if args.dry_run else "queued"
    target = "ingestion" if args.what == "pending" else "embedding"
    sys.stdout.write(f"{count} documents {verb} for {target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
