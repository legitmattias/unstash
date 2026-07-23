# ADR 0011: LLM Calls — In-Process Gateway and Call-Level Observability

## Status

Accepted.

## Context

Cluster labeling (document-type discovery) introduces the first LLM call in the product path: one call per discovered cluster, roughly 5–15 calls per organisation in total, with no per-request latency requirement. Later features (rare classification fallback, answer synthesis) will add calls, but sustained volume is not expected before answer synthesis ships.

Three operational questions follow: how calls reach providers, how the EU-only provider policy is enforced, and how calls are observed (tokens, cost, latency, outcome).

Two common answers are standing services: an LLM gateway proxy (single network endpoint, central budget enforcement) and a dedicated LLM-tracing stack. Both were evaluated and both are disproportionate at this call volume:

- The gateway proxy is another always-on container to operate, patch, and monitor, serving a handful of calls per week. The gateway library's request path is additionally in the middle of a staged rewrite, which argues against operating its server form while that lands.
- The current self-hosted release of the leading tracing stack requires roughly six services (web, worker, relational store, columnar store, queue, object store) — more infrastructure than the entire application uses today, to trace calls that fit in a single database table.

The application already has an established pattern for model inference (ADR 0009): a minimal interface, a hosted backend, a deterministic fake, selected by configuration.

## Decision

**Gateway: the LiteLLM Python SDK in-process, behind an ADR 0009-style backend.** No proxy container.

- Each LLM-consuming component defines a minimal interface with `off` / `fake` / hosted backends selected by configuration. The hosted backend calls providers through the LiteLLM SDK, so provider choice and model name are configuration values and a local runtime (e.g. Ollama) remains a config-only switch.
- **EU-hosted provider endpoints only**, enforced in the backend's configuration defaults, consistent with ADR 0009.
- **The LiteLLM version is pinned** and upgraded deliberately. The library had a supply-chain incident on its distribution channel in early 2026 and its internals are mid-rewrite; it is treated as a supply-chain-sensitive dependency.
- Credentials follow the file-based secrets pattern (ADR 0003), one secret per provider.

**Observability: an `llm_calls` table plus structured logs.** No dedicated tracing service.

- Every LLM call writes one row: organisation, purpose (e.g. `cluster_label`), model, prompt/completion token counts, cost, latency, outcome (ok / error / fallback-used). The table is org-scoped under the standard RLS policy.
- The same facts are emitted as a structured log event using OpenTelemetry GenAI semantic-convention attribute names, flowing into the existing log pipeline. Using the standard names means a dedicated tracing tool can consume the stream later without renaming.
- Per-org LLM cost becomes a queryable number, keeping the cost budget measured rather than estimated.

**Adoption triggers** — revisit this decision when either occurs:

- **Proxy form**: more than one service needs LLM access, or per-org budget caps must be enforced centrally rather than per-component (expected with answer synthesis).
- **Dedicated tracing**: sustained call volume where per-call rows and logs stop being enough — multi-step generation flows (synthesis over retrieved context) or LLM-judged evaluation runs.

## Consequences

- No new standing services; the shared host's footprint is unchanged.
- Provider policy and model choice live in configuration, reviewable in one place.
- Cost tracking starts with the first call ever made, seeding the measured cost baseline the pricing model depends on.
- Budget caps are enforced per-component (call-count bounds in application code), not centrally; acceptable while exactly one component makes calls, and an explicit trigger above.
- A future tracing tool starts from a complete, semantically standard call log rather than from zero.
