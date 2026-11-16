# Requeue lost or invalidated ingestion work

Queues embedding for every parsed document that has chunks without an embedding, or ingestion for every document that was never parsed.

## When to use

- After a migration that discards stored embeddings (a change of embedding model or vector width). Until the re-embed finishes, the affected documents are `parsed`, not `indexed`, and do not appear in search results.
- When documents are stuck in `parsed` because their embedding job was lost, for example after the worker was stopped between the parse and embed steps.

- When documents are stuck in `pending` because their ingestion task was lost, for example after the worker was killed while parsing. See [Documents stuck in pending](#documents-stuck-in-pending).

Re-embedding does not re-parse documents and does not touch documents in any other status.

## Prerequisites

- The migration, if any, has been applied and the application is running on the new configuration.
- The embedder backend the worker is configured for is reachable. For the `local` backend, the inference service must be healthy.
- Shell access to the application host.

## Procedure

Run the command in the API container, which has both the admin database credential and access to the task broker. `$PROJECT` is the compose project name of the environment.

1. See how many documents are affected:

   ```console
   docker exec $PROJECT-api-1 python -m unstash.maintenance.requeue embeddings --dry-run
   ```

2. Queue them:

   ```console
   docker exec $PROJECT-api-1 python -m unstash.maintenance.requeue embeddings
   ```

3. Follow progress. The worker logs one `embed_document_completed` line per document:

   ```console
   docker logs -f $PROJECT-worker-1 | grep embed_document
   ```

4. When the queue has drained, confirm nothing is left:

   ```console
   docker exec $PROJECT-api-1 python -m unstash.maintenance.requeue embeddings --dry-run
   ```

   It should report `0 documents would be queued for embedding`. Documents whose embedding failed are `failed`, with the error on the document; fix the cause and upload them again or reset them.

## Documents stuck in pending

A parse that dies with its worker commits nothing: the document stays `pending` and its task is gone. The command cannot tell such a document from one whose task is still waiting in the queue, so use it only when the queue is empty and the worker is idle. Queueing a document twice parses it twice, and the second parse fails on the first one's chunks.

1. Confirm the queue is empty (`0`):

   ```console
   docker exec $PROJECT-redis-1 redis-cli llen taskiq
   ```

2. Confirm the worker is idle: no `ingest_job_started` line without a matching completion in its recent log.

3. Count, then queue:

   ```console
   docker exec $PROJECT-api-1 python -m unstash.maintenance.requeue pending --dry-run
   docker exec $PROJECT-api-1 python -m unstash.maintenance.requeue pending
   ```

A document that kills the worker every time it is parsed will be lost again. Find it from the worker log and handle it separately before repeating the command.

Each requeued document gets a new job. The job of the lost task stays `queued` or `running`; it is not linked to its document and is not cleaned up by this command.

## Notes

- The command works across all organisations. Each queued task runs in the worker under its own organisation's context.
- Repeating the command is safe: the embed task only fills chunks whose embedding is still empty.
- Throughput is set by the embedder. On a CPU-only host the self-hosted embedder handles roughly five chunks per second.
- Search stays available throughout and returns the documents that have been re-embedded so far.
