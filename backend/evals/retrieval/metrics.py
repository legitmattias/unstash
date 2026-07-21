"""Ranking metrics for the retrieval evaluation harness.

Pure functions over ranked document lists and graded judgments.
Conventions (see evals/README.md): grades are 0-3; Recall@k and MRR
treat grade >= 2 as relevant; nDCG uses the graded scale.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy import stats

RELEVANT_GRADE = 2

# Confidence intervals come from a cluster bootstrap and significance from a
# paired permutation test — below a few hundred datapoints the normal
# approximation understates uncertainty. The resampling unit is the source
# document: queries on the same document are dependent, so whole documents are
# drawn together. Primary-document clustering under-captures that dependence
# (40 queries map to ~24 clusters, and topic groups couple several documents),
# so intervals stay slightly narrow — documented, not corrected.
_BOOTSTRAP_REPLICATES = 10_000
_PERMUTATIONS = 10_000
_CONFIDENCE = 0.95


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


def _grouped(values: list[float], clusters: list[str]) -> list[list[float]]:
    """Group per-query values by source document, preserving membership."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for value, cluster in zip(values, clusters, strict=True):
        grouped[cluster].append(value)
    return list(grouped.values())


def clustered_bootstrap_ci(
    values: list[float],
    clusters: list[str],
    *,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Percentile cluster-bootstrap CI for the query-level mean.

    Whole documents are drawn with replacement carrying their queries, and each
    replicate's mean is over the pooled queries — the same query-level mean as
    the reported figure, not a mean of document-means. The percentile method is
    used rather than BCa: with the 4-9 clusters a slice carries, BCa's
    bias-correction and acceleration are unreliable (and can return NaN).
    Returns ``(mean, low, high)`` at the ``_CONFIDENCE`` level.
    """
    groups = _grouped(values, clusters)
    if not groups:
        raise ValueError("cannot bootstrap an empty sample")
    sums = np.array([sum(group) for group in groups])
    sizes = np.array([len(group) for group in groups])
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(groups), size=(_BOOTSTRAP_REPLICATES, len(groups)))
    replicate_means = sums[picks].sum(axis=1) / sizes[picks].sum(axis=1)
    tail = (1.0 - _CONFIDENCE) / 2.0
    low, high = np.percentile(replicate_means, [tail * 100.0, (1.0 - tail) * 100.0])
    return float(np.mean(values)), float(low), float(high)


def clustered_paired_test(
    a_scores: list[float],
    b_scores: list[float],
    clusters: list[str],
    *,
    seed: int = 0,
) -> tuple[float, float, float, float]:
    """Paired A-minus-B difference: cluster-bootstrap CI and permutation p-value.

    The CI is the query-level mean difference from :func:`clustered_bootstrap_ci`
    over the per-query differences. The p-value comes from
    ``scipy.stats.permutation_test`` sign-flipping whole documents
    (``permutation_type="samples"``) over the per-document mean scores — the
    document is the exchangeable unit under the null. Returns
    ``(mean_diff, low, high, p_value)``.
    """
    diffs = [a - b for a, b in zip(a_scores, b_scores, strict=True)]
    mean, low, high = clustered_bootstrap_ci(diffs, clusters, seed=seed)

    a_by_doc: dict[str, list[float]] = defaultdict(list)
    b_by_doc: dict[str, list[float]] = defaultdict(list)
    for a, b, cluster in zip(a_scores, b_scores, clusters, strict=True):
        a_by_doc[cluster].append(a)
        b_by_doc[cluster].append(b)
    a_means = np.array([float(np.mean(a_by_doc[key])) for key in a_by_doc])
    b_means = np.array([float(np.mean(b_by_doc[key])) for key in a_by_doc])
    p_value = stats.permutation_test(
        (a_means, b_means),
        lambda x, y: float(np.mean(x - y)),
        permutation_type="samples",
        n_resamples=_PERMUTATIONS,
        rng=seed,
    ).pvalue
    return mean, low, high, float(p_value)
