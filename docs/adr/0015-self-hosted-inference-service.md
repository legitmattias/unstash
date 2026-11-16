# ADR 0015: Self-Hosted Embeddings and Reranking in a Separate Inference Service

## Status

Accepted. Amends [ADR 0009](./0009-config-switched-inference-backends.md): the local backend for embeddings and reranking is a separate service, not a model loaded in the API or worker process.

## Context

Embeddings and reranking were served by a hosted API. Two properties of that arrangement are unsatisfactory for document archives that contain personal data:

- Every chunk at indexing time, and every query plus its candidate excerpts at search time, leaves the deployment.
- The hosted models' open weights carry research-only licences, so the self-hosting route ADR 0009 assumed for them is not available for commercial use.

ADR 0009 already requires every inference component to have a local backend selectable by configuration. What it left open is which open-weight models to run and where.

Candidates were measured on the synthetic golden set (40 queries) and on an 8-vCPU, 16 GB, AVX2-only host without a GPU. Reports are in `backend/evals/retrieval/reports/` (`experiment-local-embedder-bakeoff.md`, `experiment-local-reranker-pairs.md`, `experiment-local-reranker-candidates.md`).

- **Embedders.** Five permissively licensed multilingual models were statistically indistinguishable from the hosted baseline in fused retrieval; only the smallest (97M parameters) was measurably worse. They differ in cost: 0.6B-parameter models need 0.6–0.9 s per query embedding on this host, 300M–570M models 0.07–0.19 s.
- **Rerankers.** Reranking adds 0.07–0.10 nDCG@10 over fusion alone. With a reranker enabled, the choice of embedder makes no measurable difference. `bge-m3` with `bge-reranker-v2-m3` scores 0.776 against 0.781 for the hosted pair. Reranking 10 candidates scores the same as 20.
- **Reranker cost.** A 568M-parameter cross-encoder takes about 3 s for 10 candidates and 5 s for 20 on this host. Int8 quantisation gains under 10% without VNNI. A 118M-parameter multilingual reranker takes 0.3 s and ties on this set, but its fine-tuning data does not include Swedish.
- **Serving.** A ready-made embedding server was tested and rejected: with default settings it reserved over 12 GB for the embedder, and it ran the reranker (no ONNX weights published) on a slower backend than sentence-transformers.

Both the API (query embedding, reranking) and the worker (chunk embedding) need the embedder. Loading models in each process, as ADR 0009 described, would hold the embedder twice and make every API restart wait for a model load.

## Decision

**Models.** `BAAI/bge-m3` (MIT, 1024 dimensions) for embeddings and `BAAI/bge-reranker-v2-m3` (Apache-2.0) for reranking, with revisions pinned. The reranker reorders the top 10 fused candidates.

**Placement.** One inference service, in the application image with a different command, holds one copy of each model and serves both over HTTP on the internal network:

- `POST /embed`, `POST /rerank`, `GET /health`. The server listens only after both models are loaded.
- It is unauthenticated, is never published outside the internal network, mounts no secrets, and has no database access.
- Inference is serialised inside the service; each call already uses every configured thread.
- The container has explicit memory and CPU limits.

**Selection.** `embedder_backend` and `reranker_backend` accept `local` beside the hosted backend and the fake. The hosted backend remains available; switching is a configuration change plus, for the embedder, a re-embed when the vector width differs. The application refuses to start when the selected embedder's width does not match the vector column.

**Failure semantics** are unchanged from ADR 0009: a rerank failure or timeout degrades to fusion order; an embedding failure fails the document's indexing attempt, which is retried.

## Consequences

- **Document text stays inside the deployment** for embeddings and reranking. OCR, entity extraction and cluster labeling are unaffected by this decision.
- **Search latency rises.** The rerank step takes seconds on a CPU-only host, against a 500 ms target for the whole search. This is accepted for now on the grounds that result quality matters more than speed at this stage; it is to be reassessed against real usage. The measured levers are fewer candidates, the smaller reranker, and switching the reranker backend by configuration.
- **About 5 GB of memory** is held by the service on the application host.
- **The vector column narrows from 2048 to 1024 dimensions**, which requires a migration and a full re-embed, and brings the column under the 2000-dimension limit of HNSW indexes.
- **A model change is a deployment change**: the service's model settings, and for the embedder also the column width and a re-embed.
- **The evidence is synthetic.** All quality numbers come from a 40-query set whose intervals are about ±0.10 wide. The choice of reranker in particular is to be re-measured on a real archive.

## Alternatives considered

- **Keep the hosted API.** Rejected: document text leaves the deployment, and the fallback to self-hosting the same weights is closed by their licences.
- **Models in the API and worker processes.** Rejected: the embedder is held twice, the API container grows by several GB, and every deploy delays search until the models reload.
- **A ready-made inference server.** Rejected on the measurements above. Worth re-testing if the reranker gains ONNX weights or the host changes.
- **GPU inference.** Not available on the host. Per-second serverless GPU fits the volume but adds cold starts of tens of seconds to interactive search; an always-on GPU is disproportionate at this scale.
- **A smaller reranker by default.** Deferred: it is five times faster and ties on the synthetic set, but was not trained on Swedish. It is a candidate once quality can be measured on real queries.
- **No reranker.** Rejected: fusion alone scores 0.07–0.10 lower.
