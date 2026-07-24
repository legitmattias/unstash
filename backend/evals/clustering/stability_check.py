"""Re-cluster stability under corpus growth.

Same-corpus re-runs are deterministic (pinned UMAP seed, stored
embeddings), so the meaningful stability question is: when the archive
grows and the doubling trigger re-clusters, how much do assignments of
*already-clustered* documents churn?

Simulates growth by clustering prefixes of a fixed random document order
(60% → 80% → 100%) and reporting ARI between consecutive stages restricted
to the shared documents. High ARI = labels survive growth; low ARI = users
see categories reshuffle as they add files.

    python evals/clustering/stability_check.py           # uses cached embeddings
    python evals/clustering/stability_check.py --embedder fake  # plumbing smoke
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND / "src"))

import numpy as np
from run_eval import load_corpus_cached

STAGES = (0.6, 0.8, 1.0)
ORDER_SEED = 7


async def run(embedder_kind: str, selection: str) -> None:
    from sklearn.metrics import adjusted_rand_score

    from unstash.clustering.engine import cluster_documents

    names, texts, embeddings, _truth = await load_corpus_cached(embedder_kind)
    n = len(names)
    order = np.random.default_rng(ORDER_SEED).permutation(n)

    print(f"corpus: {n} documents; stages: {[int(n * s) for s in STAGES]}; selection={selection}\n")

    stage_assignments: list[dict[int, int]] = []
    for fraction in STAGES:
        size = int(n * fraction)
        idx = order[:size]
        result = cluster_documents(
            [texts[i] for i in idx],
            embeddings[idx],
            cluster_selection_method=selection,
        )
        assignment = {int(doc): a for doc, a in zip(idx, result.assignments, strict=True)}
        noise = sum(1 for a in result.assignments if a == -1)
        clusters = len(result.keywords)
        print(f"  stage {int(fraction * 100):>3}%: {size} docs, {clusters} clusters, {noise} noise")
        stage_assignments.append(assignment)

    print("\npairwise ARI on shared documents (consecutive stages):")
    for prev, curr, frac in zip(stage_assignments, stage_assignments[1:], STAGES[1:], strict=False):
        shared = sorted(set(prev) & set(curr))
        ari = adjusted_rand_score([prev[d] for d in shared], [curr[d] for d in shared])
        print(f"  → {int(frac * 100)}%: shared={len(shared)}  ARI={ari:.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", choices=["jina", "fake"], default="jina")
    parser.add_argument("--selection", choices=["eom", "leaf"], default="eom")
    args = parser.parse_args()
    asyncio.run(run(args.embedder, args.selection))
