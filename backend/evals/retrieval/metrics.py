"""Ranking metrics for the retrieval evaluation harness.

Pure functions over ranked document lists and graded judgments.
Conventions (see evals/README.md): grades are 0-3; Recall@k and MRR
treat grade >= 2 as relevant; nDCG uses the graded scale.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict

RELEVANT_GRADE = 2

# Defaults for the resampling machinery. Below a few hundred datapoints the
# normal approximation understates uncertainty, so intervals come from the
# bootstrap and significance from a permutation test — not a standard-error
# formula. Queries that share a source document move together, so the
# resampling unit is the cluster (source document), never the query.
_BOOTSTRAP_REPLICATES = 10_000
_PERMUTATIONS = 10_000
_ALPHA = 0.05


def recall_at_k(ranked: list[str], judgments: dict[str, int], k: int) -> float:
    """Fraction of relevant (grade >= 2) documents present in the top k."""
    relevant = {doc for doc, grade in judgments.items() if grade >= RELEVANT_GRADE}
    if not relevant:
        raise ValueError("recall is undefined for a query with no relevant documents")
    hits = sum(1 for doc in ranked[:k] if doc in relevant)
    return hits / len(relevant)


def mrr(ranked: list[str], judgments: dict[str, int]) -> float:
    """Reciprocal rank of the first relevant (grade >= 2) document; 0 if absent."""
    for position, doc in enumerate(ranked, start=1):
        if judgments.get(doc, 0) >= RELEVANT_GRADE:
            return 1.0 / position
    return 0.0


def dcg_at_k(ranked: list[str], judgments: dict[str, int], k: int) -> float:
    """Discounted cumulative gain over the top k, using graded relevance."""
    return sum(
        (2 ** judgments.get(doc, 0) - 1) / math.log2(position + 1)
        for position, doc in enumerate(ranked[:k], start=1)
    )


def ndcg_at_k(ranked: list[str], judgments: dict[str, int], k: int) -> float:
    """DCG normalised by the ideal ordering's DCG; 0 if no graded documents."""
    ideal = sorted(judgments.values(), reverse=True)[:k]
    ideal_dcg = sum(
        (2**grade - 1) / math.log2(position + 1) for position, grade in enumerate(ideal, start=1)
    )
    if ideal_dcg == 0:
        return 0.0
    return dcg_at_k(ranked, judgments, k) / ideal_dcg


def _by_cluster(values: list[float], clusters: list[str]) -> list[list[float]]:
    """Group per-query values by their cluster key, preserving membership."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for value, cluster in zip(values, clusters, strict=True):
        grouped[cluster].append(value)
    return list(grouped.values())


def clustered_bootstrap_ci(
    values: list[float],
    clusters: list[str],
    *,
    replicates: int = _BOOTSTRAP_REPLICATES,
    alpha: float = _ALPHA,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Percentile bootstrap CI for a mean, resampling whole clusters.

    ``clusters`` is the per-query grouping key (source document). Whole
    clusters are drawn with replacement so dependence between queries on
    the same document is respected. Returns ``(mean, low, high)`` at the
    ``1 - alpha`` level. Used both for a single config's slice mean and,
    by passing per-query paired differences, for an A-minus-B comparison.
    """
    if not values:
        msg = "cannot bootstrap an empty sample"
        raise ValueError(msg)
    groups = _by_cluster(values, clusters)
    rng = random.Random(seed)
    n_clusters = len(groups)
    means: list[float] = []
    for _ in range(replicates):
        total = 0.0
        count = 0
        for _ in range(n_clusters):
            drawn = groups[rng.randrange(n_clusters)]
            total += sum(drawn)
            count += len(drawn)
        means.append(total / count)
    means.sort()
    mean = sum(values) / len(values)
    low = means[int((alpha / 2) * replicates)]
    high = means[min(replicates - 1, int((1 - alpha / 2) * replicates))]
    return mean, low, high


def clustered_permutation_pvalue(
    diffs: list[float],
    clusters: list[str],
    *,
    permutations: int = _PERMUTATIONS,
    seed: int = 0,
) -> float:
    """Two-sided paired permutation p-value for mean(diffs) = 0.

    ``diffs`` are per-query paired differences (config A minus config B);
    ``clusters`` is the per-query grouping key. Each cluster's differences
    flip sign together (the exchangeable unit under the null), so
    within-document dependence does not inflate significance. Add-one
    smoothing keeps the p-value strictly positive.
    """
    if not diffs:
        msg = "cannot test an empty sample"
        raise ValueError(msg)
    cluster_sums = [sum(group) for group in _by_cluster(diffs, clusters)]
    n = len(diffs)
    observed = abs(sum(diffs) / n)
    rng = random.Random(seed)
    at_least = 0
    for _ in range(permutations):
        total = sum(s if rng.random() < 0.5 else -s for s in cluster_sums)
        if abs(total / n) >= observed - 1e-12:
            at_least += 1
    return (at_least + 1) / (permutations + 1)
