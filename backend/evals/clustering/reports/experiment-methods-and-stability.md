# Experiment: clustering methods and growth stability

**Date:** 2026-07-24 · **Scripts:** `method_bakeoff.py`, `stability_check.py` · **Data:** cached jina-embeddings-v4 document vectors for the synthetic corpus (46 docs, 10 ground-truth types; 3 types are singletons, so ~6 types are separable at `min_cluster_size=3`). Evidence for ADR 0013.

## Method bake-off (identical embeddings, vs ground-truth types)

| method | clusters | noise | ARI | NMI | purity |
|---|---|---|---|---|---|
| engine — HDBSCAN/eom | 2 | 0 | 0.351 | 0.472 | 0.565 |
| engine — HDBSCAN/leaf | 6 | 15 | **0.437** | **0.714** | **0.774** |
| K-means (silhouette k=4) | 4 | 0 | 0.435 | 0.607 | 0.630 |
| Ward (silhouette k=3) | 3 | 0 | **0.470** | 0.562 | 0.609 |

Baselines run on L2-normalized raw embeddings (no UMAP), k swept 2–10 by cosine silhouette — the method as it would plausibly have been built.

**At this corpus size the classical baselines are competitive**: Ward wins ARI outright with zero noise; K-means matches leaf's ARI at full coverage. Leaf leads NMI/purity but leaves 15/46 documents unassigned. No method reaches the ~6 separable types; silhouette-chosen k lands at 3–4, mirroring EOM's coarseness.

## Growth stability (cluster prefixes of a fixed random order; ARI on shared docs)

| stage | eom | leaf |
|---|---|---|
| 60% (27 docs) | 2 clusters, **20 noise** | 2 clusters, 20 noise |
| 80% (36 docs) | 3 clusters, 0 noise | 6 clusters, 6 noise |
| 100% (46 docs) | 3 clusters, 0 noise | 6 clusters, 12 noise |
| ARI 60→80 | −0.130 | 0.028 |
| ARI 80→100 | **0.870** | 0.446 |

- Below ~30 documents the engine emits mostly noise — empirical support for the 50-document trigger (`clustering_min_documents`); the engine's hard floor of 10 is a crash guard, not a quality bar.
- Once past ~36 documents, **EOM assignments are stable under growth (0.870)**; **leaf reshuffles nearly half its structure (0.446)** — label continuity, a milestone success criterion, strongly favours EOM.
- Full-corpus EOM produced 3 clusters here vs 2 in the bake-off on the same vectors: UMAP is input-order-sensitive. Production order is deterministic per org (created_at, id), so per-org results are reproducible, but the boundary sensitivity at this scale is real.

## Decision inputs

1. EOM stays the production extraction: full coverage + 2× leaf's growth stability, at a known cost in granularity.
2. HDBSCAN keeps its place on architectural grounds (no per-org k sweeps, noise handling, hierarchy for future product structure) — **not** on measured quality superiority at pilot scale, where Ward/K-means are honest peers. Revisit trigger: if the real-set reality check or larger corpora show silhouette-swept classical baselines consistently ahead, the engine seam makes switching cheap.
3. Growth-stability (this script) joins the eval repertoire; re-run when parameters or extraction change.
