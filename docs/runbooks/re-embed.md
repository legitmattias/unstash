# Re-embed documents

Queues embedding for every parsed document that has chunks without an embedding.

## When to use

- After a migration that discards stored embeddings (a change of embedding model or vector width). Until the re-embed finishes, the affected documents are `parsed`, not `indexed`, and do not appear in search results.
- When documents are stuck in `parsed` because their embedding job was lost, for example after the worker was stopped between the parse and embed steps.

It does not re-parse documents and does not touch documents in any other status.

## Prerequisites

- The migration, if any, has been applied and the application is running on the new configuration.
- The embedder backend the worker is configured for is reachable. For the `local` backend, the inference service must be healthy.
- Shell access to the application host.

## Procedure

Run the command in the API container, which has both the admin database credential and access to the task broker. `$PROJECT` is the compose project name of the environment.

1. See how many documents are affected:

   ```console
   docker exec $PROJECT-api-1 python -m unstash.maintenance.requeue_embeddings --dry-run
   ```

2. Queue them:

   ```console
   docker exec $PROJECT-api-1 python -m unstash.maintenance.requeue_embeddings
   ```

3. Follow progress. The worker logs one `embed_document_completed` line per document:

   ```console
   docker logs -f $PROJECT-worker-1 | grep embed_document
   ```

4. When the queue has drained, confirm nothing is left:

   ```console
   docker exec $PROJECT-api-1 python -m unstash.maintenance.requeue_embeddings --dry-run
   ```

   It should report `0 documents would be queued for embedding`. Documents whose embedding failed are `failed`, with the error on the document; fix the cause and upload them again or reset them.

## Notes

- The command works across all organisations. Each queued task runs in the worker under its own organisation's context.
- Repeating the command is safe: the embed task only fills chunks whose embedding is still empty.
- Throughput is set by the embedder. On a CPU-only host the self-hosted embedder handles roughly five chunks per second.
- Search stays available throughout and returns the documents that have been re-embedded so far.
