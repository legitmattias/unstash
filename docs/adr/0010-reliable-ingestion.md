# ADR 0010: Reliable document ingestion

## Status

Accepted. Implementation is phased and tracked in the reliable-ingestion epic
(GitHub issue #146); individual phases may land over time, but the model below
is the decision of record.

## Context

Ingestion turns an uploaded file into an indexed, searchable document through a
chain of steps, several of which call **external, rate-limited services**:
embeddings and reranking (one shared provider quota), NER, and OCR. These
services return `429` (rate limit) or `5xx`/timeouts under load or during
cold starts. A bulk upload — dozens of documents at once today, whole cloud
drives once connectors land — bursts these calls and trips the limits.

The current pipeline is not resilient to this:

- A failed step marks the document `failed` **terminally**. There is no retry
  beyond a few seconds of in-process attempts, and `Retry-After` is ignored.
- Nothing recovers a document left in a non-terminal state (e.g. a worker
  crash between steps, or a lost queue message). It simply sits there.
- All failures are lumped together — a transient rate limit is treated the
  same as a genuinely unprocessable file.

The result is silent data loss: documents that should have indexed are dropped
with no automatic recovery and no queue of things a human should look at. For a
document-search product, that is unacceptable — the corpus must be complete and
trustworthy.

## Decision

Adopt the standard durable-pipeline model, implemented on the existing stack
(Taskiq + Redis for dispatch, PostgreSQL for state). For anyone familiar with
RabbitMQ, Kafka, or NATS JetStream, the shape is the same one those systems
formalise; we implement it explicitly rather than adopt a heavier broker:

**1. PostgreSQL is the source of truth, not the queue.**
Every document's state lives in the database (`documents.status` + a job
record). Redis is only a dispatch mechanism. We never rely on the broker for
durability or for delayed delivery. This is what makes "no document is
silently lost" achievable even if Redis drops a message or a worker dies
mid-task.

**2. At-least-once delivery with idempotent consumers.**
A task may run more than once; that must be safe. This is already true — the
embed step commits partial progress and finishes remaining chunks on a re-run,
and metadata upserts by document id. Idempotency is the prerequisite that makes
every retry and re-drive below safe, and it is the expensive half that is
already done.

**3. A retry ladder, not a single attempt.**
- *In-band retry* absorbs brief blips: capped exponential backoff **with
  jitter**, honoring `Retry-After`.
- *Scheduled retry* handles sustained failures: instead of failing, the job is
  rescheduled with growing backoff (persisted as `next_retry_at` on the job
  record), so a longer rate-limit window or provider blip is waited out.
- *Dead-letter* is the floor: after a bounded auto-retry budget the document
  moves to a durable `needs_attention` state for manual re-drive.

**4. Errors are classified.**
Transient (`429`, `5xx`, timeout, network) ride the ladder. Permanent
(unsupported format, corrupt file, `4xx` bad input) skip it and go straight to
manual review — no point spending the retry budget on something that cannot
succeed.

**5. One DB-driven reconciler.**
A single periodic job scans the database and re-enqueues both (a) retries whose
`next_retry_at` is due and (b) orphaned jobs stuck in a non-terminal state past
a threshold. This one mechanism covers scheduled retries *and* crash/lost-message
recovery, and — unlike broker-native delays — it survives restarts because the
schedule lives in Postgres.

**6. Prevent, then protect.**
A client-side rate limiter (token bucket over requests-per-minute and
tokens-per-minute, shared across embeddings and reranking since they draw from
one quota) keeps us under the tier so most `429`s never happen. A circuit
breaker makes a hard provider outage back off as a group instead of every job
hammering a dead endpoint.

### How long before a document reaches manual review?

A policy, config-driven. The provider's rate limits reset per minute, so real
rate-limit failures recover in seconds to minutes. A failure that persists for
hours indicates something structural (outage, quota, billing, revoked key) that
a human should see. So the auto-retry budget rides out a provider blip — on the
order of a few hours of backoff — then hands off to the dead-letter state.
Permanent errors go to manual review immediately.

## Rollout

Phased, lowest-risk first, tracked in issue #146:

1. **Prevent** — rate limiter, `Retry-After`, backoff + jitter (#145).
2. **Durability core** — error classification, scheduled retry, reconciler.
3. **Dead-letter + manual retry** — `needs_attention` state, admin re-drive.
4. **Observability + circuit breaker** — failure / dead-letter-depth metrics,
   alerts, breaker.

## Alternatives considered

- **Broker-native retries and delayed delivery (Taskiq/Redis features only).**
  Rejected as the sole mechanism: an in-memory delay does not survive a restart,
  and a Redis list broker gives no crash recovery for a message lost between
  delivery and completion. The DB-of-record plus reconciler covers both; broker
  features can still be used where convenient, but are not load-bearing.
- **Adopt a heavier broker (RabbitMQ / Kafka / NATS JetStream) for native
  dead-letter and redelivery.** Rejected for now: it adds an operational
  component for guarantees we can get from the database we already run at the
  current scale. Revisit if ingestion throughput outgrows a single Redis-backed
  worker.
- **Do nothing / accept terminal failures.** Rejected — silent loss of indexed
  documents is a correctness failure for a search product.

## Consequences

- No silent data loss: every document is either indexed, scheduled for retry, or
  visibly awaiting manual review. Bulk ingestion becomes safe under the paid
  tier.
- More moving parts: retry/schedule columns on the job record, a reconciler job,
  an admin re-drive surface, and the rate-limiter/breaker in the provider client.
- The reconciler must itself be cheap and idempotent, and its thresholds tuned so
  it recovers orphans without fighting in-flight work.

## Intentionally deferred (documented, not skipped)

- **Cross-document embedding batching** and a **rate-limited bulk/backfill path**
  — meaningful when connectors sync large document sets; deferred to that work,
  tracked in #146 and #145. Not required for interactive uploads.
- **Metrics-stack integration** (Grafana/Prometheus) — failure and dead-letter
  metrics wire in when the observability stack lands; structured logs cover the
  interim.
