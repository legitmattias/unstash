"""Queue embedding for parsed documents that have unembedded chunks.

    python -m unstash.maintenance.requeue_embeddings [--dry-run]

Needed after a change of embedding model or vector width, which leaves
every document ``parsed`` with NULL embeddings, and after an embedding
job was lost between parse and embed.

The command works across organisations, so it uses the admin database
role and must run where that credential and the task broker are both
available. Each queued task runs in the worker under its own
organisation's context, exactly as after an upload.

Repeating the command is safe: the embed task only fills chunks whose
embedding is still NULL. Running it while documents are being ingested
can queue a second embed for a document whose first is in flight; the
duplicate finds nothing left to do or repeats identical work.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import exists, select, update

from unstash.db.engine import dispose_engine
from unstash.db.models import Chunk, Document, DocumentStatus, JobProgress, JobStatus
from unstash.db.session import get_admin_sessionmaker


async def requeue_embeddings(*, dry_run: bool) -> int:
    """Queue an embed task per eligible document; return how many."""
    # Imported here: importing the tasks package builds the broker.
    from unstash.tasks.ingest import embed_document  # noqa: PLC0415

    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        # Deliberately not filtered by org_id: a maintenance sweep over
        # every organisation, on the admin role.
        result = await session.execute(
            select(Document.id, Document.org_id)
            .where(
                Document.status == DocumentStatus.PARSED,
                exists().where(
                    Chunk.document_id == Document.id,
                    Chunk.embedding.is_(None),
                ),
            )
            .order_by(Document.created_at),
        )
        targets = [(row.id, row.org_id) for row in result]
        if dry_run or not targets:
            return len(targets)

        jobs: list[tuple[uuid.UUID, uuid.UUID, JobProgress]] = []
        for document_id, org_id in targets:
            job = JobProgress(
                org_id=org_id,
                # Unique placeholder, replaced with the broker's id below.
                task_id=f"requeue-{uuid.uuid4()}",
                task_name="embed_document",
                status=JobStatus.QUEUED,
            )
            session.add(job)
            jobs.append((document_id, org_id, job))
        # Committed before sending so the worker finds its job row.
        await session.commit()

        for document_id, org_id, job in jobs:
            sent = await embed_document.kiq(str(org_id), str(document_id), str(job.id))
            await session.execute(
                update(JobProgress).where(JobProgress.id == job.id).values(task_id=sent.task_id),
            )
        await session.commit()
        return len(targets)


async def _run(*, dry_run: bool) -> int:
    from unstash.tasks import broker  # noqa: PLC0415

    await broker.startup()
    try:
        return await requeue_embeddings(dry_run=dry_run)
    finally:
        await broker.shutdown()
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m unstash.maintenance.requeue_embeddings",
        description=(
            "Queue embedding for every parsed document that has chunks without an embedding."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many documents would be queued, without queueing them.",
    )
    args = parser.parse_args(argv)
    count = asyncio.run(_run(dry_run=args.dry_run))
    verb = "would be queued" if args.dry_run else "queued"
    sys.stdout.write(f"{count} documents {verb} for embedding\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
