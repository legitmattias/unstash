# ADR 0009: Config-Switched Inference Backends (Hosted API First, Local Alternative)

## Status

Accepted.

## Context

Several pipeline components are model-inference tasks: search-result reranking (cross-encoder), OCR for scanned PDFs, and named-entity recognition. Running these models in-process is memory-expensive — each adds hundreds of MB to several GB of resident model weights, and the current deployment budget cannot hold all of them alongside the document parser.

At the same time, pay-per-use hosted inference is available for every one of them from EU-jurisdiction providers, at costs proportionate to pilot-scale volume (cents to single-digit euros per month). Two of the providers are already part of the stack: the embedding provider offers a reranking API, and the LLM provider offers a document-OCR API. On CPU-only hosts, hosted GPU inference is also typically *faster* than running the same model locally.

The embedding client already established the pattern this decision generalises: a minimal interface, a real hosted client, a deterministic fake for tests, and a configuration setting selecting the backend.

## Decision

Every model-inference component is implemented behind a **minimal interface with interchangeable backends selected by configuration** — never hard-wired to one execution location:

- A **hosted API backend** — the default at pilot scale.
- A **local in-process backend** — first-class, for self-hosting when volume economics, latency, or independence favour it.

Constraints on hosted backends:

- **EU jurisdiction is mandatory**, consistent with the existing data posture. Each hosted backend adds its provider to the sub-processor list.
- Per-call **cost and token/page usage is logged**, so the hosted-vs-local economics stay observable rather than assumed.
- Credentials follow the existing file-based secrets pattern, one secret per provider.

Component bindings and failure semantics:

| Component | Hosted backend | Local backend | On backend failure |
|---|---|---|---|
| Reranking (search) | Rerank API of the embedding provider | Cross-encoder in the API process | Degrade to fusion-ranked results; log |
| OCR (scanned documents) | Document-OCR API of the LLM provider | OCR engine via the parsing library | Document `failed` with actionable error (a scanned document without OCR has no content) |
| NER (entity metadata) | Hosted inference endpoint (scale-to-zero) | In-process model in the worker | Best-effort: log warning, document proceeds |

All models involved are open-weight, so moving a component from hosted to local (or back) is a configuration change plus deployment sizing — not a rewrite.

## Consequences

- **No feature is gated on hardware.** Memory-heavy capabilities ship at pilot scale on modest hosts, and self-hosting becomes a deliberate later decision driven by measured volume.
- **More external dependencies at runtime.** Mitigated by per-component failure semantics above, retry-with-backoff in every client, and the local backends as documented alternatives.
- **Latency**: the reranking round-trip sits on the query path (~100–300 ms within the EU); acceptable against the search latency budget, and typically better than CPU inference of the same model. OCR and NER run in background ingestion where latency is immaterial.
- **Sub-processor surface grows.** Each hosted backend is disclosed in the sub-processor list; document content already flows to the embedding provider, so this extends an accepted posture rather than creating a new one.
- **Testing**: every interface keeps a deterministic fake; hosted backends get contract tests gated on their API key being present, mirroring the embedding client's live test.

## Alternatives considered

- **Self-host everything on a larger server now.** Rejected for the pilot phase: pays a fixed monthly cost to unblock features that per-use APIs bridge for less, and couples feature delivery to an infrastructure migration.
- **Hosted-only, no local backends.** Rejected: locks the architecture to third parties and forfeits the open-weight models' main strategic property. The interface cost of keeping both is small (demonstrated by the embedding client).
- **Non-EU inference providers.** Rejected on data-posture grounds regardless of price or quality.
