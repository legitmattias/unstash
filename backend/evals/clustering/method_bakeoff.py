"""Clustering method bake-off: the production engine vs classical baselines.

Compares, on identical cached embeddings and against the corpus's
ground-truth types:

- HDBSCAN via the production engine (UMAP-reduced, eom and leaf extraction)
- K-means on L2-normalized embeddings, k chosen by silhouette sweep
- Ward agglomerative on L2-normalized embeddings, k chosen by silhouette

The baselines deliberately skip UMAP — they represent the method as it
would plausibly have been built ("naive K-means"), not HDBSCAN with a
different extractor. Evidence for the clustering-method ADR.

    python evals/clustering/method_bakeoff.py            # uses cached embeddings
    python evals/clustering/method_bakeoff.py --embedder fake   # plumbing smoke
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND / "src"))

import numpy as np
from run_eval import load_corpus_cached, purity

K_RANGE = range(2, 11)


def normalized(embeddings: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    return embeddings / np.maximum(norms, 1e-12)


def best_k_labels(embeddings: np.ndarray, method: str) -> tuple[list[int], int, float]:
    """Fit the method for each k, return labels at the silhouette-best k."""
    from sklearn.cluster import AgglomerativeClustering, KMeans
    from sklearn.metrics import silhouette_score

    best = None
    for k in K_RANGE:
        if method == "kmeans":
            model = KMeans(n_clusters=k, n_init=10, random_state=42)
        else:
            model = AgglomerativeClustering(n_clusters=k, linkage="ward")
        labels = model.fit_predict(embeddings)
        score = silhouette_score(embeddings, labels, metric="cosine")
        if best is None or score > best[2]:
            best = (list(labels), k, score)
    return best


def report_row(name: str, assignments: list[int], truth: list[str]) -> str:
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    non_noise = [(a, t) for a, t in zip(assignments, truth, strict=True) if a != -1]
    ari = adjusted_rand_score([t for _, t in non_noise], [a for a, _ in non_noise])
    nmi = normalized_mutual_info_score([t for _, t in non_noise], [a for a, _ in non_noise])
    pur = purity(assignments, truth)
    clusters = len({a for a in assignments if a != -1})
    noise = sum(1 for a in assignments if a == -1)
    return (
        f"  {name:<24} clusters={clusters:>2}  noise={noise:>2}"
        f"  ARI={ari:.3f}  NMI={nmi:.3f}  purity={pur:.3f}"
    )


async def run(embedder_kind: str) -> None:
    from unstash.clustering.engine import cluster_documents

    names, texts, embeddings, truth = await load_corpus_cached(embedder_kind)
    print(f"corpus: {len(names)} documents, {len(set(truth))} ground-truth types")
    print(f"type sizes: {dict(Counter(truth).most_common())}\n")

    for method in ("eom", "leaf"):
        result = cluster_documents(texts, embeddings, cluster_selection_method=method)
        print(report_row(f"engine (hdbscan/{method})", result.assignments, truth))

    norm = normalized(embeddings)
    for method in ("kmeans", "ward"):
        labels, k, silhouette = best_k_labels(norm, method)
        print(report_row(f"{method} (k={k}, sil={silhouette:.2f})", labels, truth))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", choices=["jina", "fake"], default="jina")
    args = parser.parse_args()
    asyncio.run(run(args.embedder))
