# Document ingestion — failure modes and recovery

## Purpose

Diagnose and recover documents that fail or stall in the ingestion pipeline. Covers the flow from upload to `indexed`.

## Pipeline overview

```
POST /api/orgs/{slug}/documents
  → file streamed to disk, SHA-256 computed
  → duplicate check (same hash, same org, non-failed → returns existing doc, HTTP 200)
  → daily-upload-limit check (429 when reached)
  → documents row (status=pending) + job_progress row (queued) + task queued
worker: ingest_document
  → MIME sniffed (content-based; overrides declared type)
  → strategy: EXTRACT | CONVERT_THEN_EXTRACT (via Gotenberg) | METADATA_ONLY | SKIP
  → parse → chunks (NULL embedding) → metadata (dates/amounts, best-effort)
  → status=parsed, queues embed_document
worker: embed_document
  → embeds NULL-embedding chunks in batches (idempotent)
  → status=indexed
```

Terminal states: `indexed` (success) and `failed` (with the reason in `documents.parsing_error`). Everything else is transient.

## Where to look

- **Document state:** `GET /api/orgs/{slug}/documents/{id}` — status, `parsing_error`, pipeline provenance.
- **Job state:** `GET /api/orgs/{slug}/jobs/{id}` — lifecycle timestamps and error.
- **Worker logs:** structured events `ingest_job_started`, `ingest_strategy_selected`, `ingest_parse_*`, `ingest_conversion_*`, `metadata_extracted`, `embed_document_*` — each carries `document_id`, duration, and peak RSS.

## The universal retry

**Re-uploading the same file is the supported retry for any failed document.** The duplicate check deliberately ignores `failed` rows, so a re-upload starts a fresh ingestion. No manual requeue tooling is needed for routine failures.

## Failure modes

### Document `failed`, parsing_error names the file

`Unsupported MIME type: ...` — the content-based sniff routed it to SKIP (unknown or suspicious type). Expected for executables/misc binaries; the user should supply a supported format.

Corrupt-file errors (parser exception class + message) — the file is damaged or not what its extension claims. Re-export at the source and re-upload.

### Document `failed`, error mentions Gotenberg / conversion

The legacy-office conversion sidecar was unreachable or rejected the file.

1. Check the sidecar: `docker ps` for the gotenberg container; its healthcheck hits `:3000/health`.
2. If down/unhealthy: `docker compose -p <project> ... up -d gotenberg`, then re-upload the document.
3. If healthy and one specific file keeps failing: the file likely lacks a usable extension (conversion selects its import filter from the filename) or is corrupt.

### Document `failed` at the embedding stage

The embedding API was unavailable past the client's retry budget (the error names the provider and status).

1. Transient outages: re-upload once the provider recovers.
2. Persistent 401/403: the API-key secret is missing or rotated — verify the secret file mounted in the worker container.
3. Persistent 429: batch volume exceeds the provider plan; stagger uploads or raise the plan.

### Document stuck in `pending` or `parsing`

The worker isn't processing (crashed, restarting, or wedged on a heavy parse).

1. `docker ps` — is the worker container running and stable (not restart-looping)?
2. Worker logs — an OOM kill shows as the container dying mid-`ingest_parse_starting`; the memory limit is set in the compose override.
3. Restart the worker: tasks are at-least-once; an interrupted parse re-runs, an interrupted embed resumes (only NULL-embedding chunks are embedded).
4. Still stuck with the worker healthy: check Redis (`docker exec <redis> redis-cli ping`) — the queue lives there.

Note: a document stuck in a non-failed state blocks duplicate re-uploads (dedup matches it). If a row must be abandoned, set it `failed` manually, then re-upload:

```sql
UPDATE documents SET status='failed', parsing_error='operator: abandoned stuck ingestion'
WHERE id = '<document-id>';
```

### Upload rejected with 429

The org hit its `daily_upload_limit` (counts rows created since UTC midnight; failed ingestions count, duplicates don't). Raise or clear it:

```sql
UPDATE organisations SET daily_upload_limit = NULL WHERE slug = '<slug>';
```

### Upload rejected with 413

File exceeds `UNSTASH_MAX_UPLOAD_BYTES` (default 100 MB). Raise the env var in the compose override if the limit is wrong for the deployment.

### Disk pressure

- Uploaded originals + converted PDFs live on the documents data volume — grow it per the VPS maintenance runbook.
- The worker's model cache lives in a named Docker volume (~GBs after first parse); it is safe to delete — models re-download on next use.

## Verification after recovery

Upload a small text file and confirm it reaches `indexed` with a `succeeded` job, then re-upload the same file and confirm `duplicate: true` — this exercises worker, queue, parser, embedder, and dedup in one pass.
