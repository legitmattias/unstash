# ADR 0013: Clustering Method — HDBSCAN with Stable Extraction, Held Honestly Against Classical Baselines

## Status

Accepted.

## Context

Document-type discovery clusters each organisation's documents from their stored embeddings (mean-pooled to document level). The method question — density-based hierarchy (BERTopic's UMAP + HDBSCAN) versus fixed-k classical clustering (K-means, Ward agglomerative) — was originally argued from general properties. This ADR records the decision against measurement instead.

Measured on the synthetic evaluation corpus (46 documents, ~6 separable ground-truth types) with identical embeddings (`evals/clustering/`, methods-and-stability report):

- **Quality parity at pilot scale.** Ward (silhouette-chosen k=3) achieved the best ARI (0.470, zero unassigned documents); K-means (k=4) matched HDBSCAN-leaf's ARI at full coverage. HDBSCAN-leaf led NMI/purity but left a third of documents unassigned. No method separated all separable types; every method's chosen granularity landed at 2–4 clusters.
- **Extraction dominates the quality trade-off within HDBSCAN.** `eom` (stability-favouring): full coverage, coarse. `leaf`: finer and purer, but 15/46 noise.
- **Growth stability separates the options decisively.** Re-clustering under simulated corpus growth (60% → 80% → 100%), ARI on shared documents: eom 0.870, leaf 0.446. Below ~30 documents every method degenerates (mostly noise) — empirically supporting the 50-document clustering trigger.

## Decision

**HDBSCAN (via BERTopic) with `eom` extraction remains the production method** — with the explicit record that this is an *architectural* choice, not a measured quality victory over classical baselines at current corpus sizes:

1. **No per-organisation k.** Cluster count adapts to each org's corpus automatically; fixed-k methods would need a silhouette sweep per org per re-cluster — feasible, but more machinery for no measured gain.
2. **Noise is a first-class outcome.** One-off documents stay unassigned rather than polluting a category; fixed-k methods force-assign everything.
3. **The hierarchy is retained.** The same fitted tree supports finer extraction and future hierarchical/faceted presentation without re-architecting.
4. **`eom` over `leaf`** for the two properties users experience directly: every document gets a category, and categories survive corpus growth (0.870 vs 0.446 ARI).

The engine exposes the extraction method as a parameter, and the evaluation harness (`method_bakeoff.py`, `stability_check.py`) keeps the baselines runnable on cached embeddings.

**Revisit triggers:**

- The real-corpus reality check or larger tenant corpora show silhouette-swept classical baselines consistently ahead — switching is cheap behind the engine seam.
- Corpus sizes grow to where leaf's fragmentation resolves (expected at hundreds of documents per type): re-run the bake-off and reconsider granularity.
- A hierarchical or faceted category surface ships: extraction strategy becomes a product decision, not only a quality one.

## Consequences

- Coarse early categories are accepted at pilot scale; the measured record shows every method is coarse there, and coverage plus stability were prioritised.
- The bake-off harness makes future method claims cheap to verify — no clustering decision needs to be argued from reputation again.
- The 50-document trigger is now empirically grounded, not just a heuristic.
