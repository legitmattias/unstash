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

# Statistics via scipy.stats: BCa bootstrap intervals and paired permutation
# tests, appropriate below a few hundred datapoints where the normal
# approximation understates uncertainty. Queries sharing a source document are
# dependent, so scores collapse to one value per document before resampling.
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


def _cluster_means(values: list[float], clusters: list[str]) -> np.ndarray:
    """Collapse per-query values to one mean per source document."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for value, cluster in zip(values, clusters, strict=True):
        grouped[cluster].append(value)
    return np.array([float(np.mean(group)) for group in grouped.values()])


def clustered_bootstrap_ci(
    values: list[float],
    clusters: list[str],
    *,
    seed: int = 0,
) -> tuple[float, float, float]:
    """BCa bootstrap CI for a mean, with the source document as the unit.

    Scores collapse to one value per document (``clusters``) before
    ``scipy.stats.bootstrap`` resamples, so queries on the same document are
    not treated as independent. Returns ``(mean, low, high)`` at 95%.
    """
    per_document = _cluster_means(values, clusters)
    interval = stats.bootstrap(
        (per_document,),
        np.mean,
        confidence_level=_CONFIDENCE,
        method="BCa",
        n_resamples=_BOOTSTRAP_REPLICATES,
        rng=seed,
    ).confidence_interval
    return float(per_document.mean()), float(interval.low), float(interval.high)


def clustered_paired_test(
    a_scores: list[float],
    b_scores: list[float],
    clusters: list[str],
    *,
    seed: int = 0,
) -> tuple[float, float, float, float]:
    """Paired A-minus-B difference with a BCa CI and a permutation p-value.

    Both configs collapse to one value per source document, so the document
    is the paired unit: ``scipy.stats.permutation_test`` sign-flips whole
    documents (``permutation_type="samples"``) and the bootstrap resamples
    them. Returns ``(mean_diff, low, high, p_value)``.
    """
    a = _cluster_means(a_scores, clusters)
    b = _cluster_means(b_scores, clusters)
    diff = a - b
    interval = stats.bootstrap(
        (diff,),
        np.mean,
        confidence_level=_CONFIDENCE,
        method="BCa",
        n_resamples=_BOOTSTRAP_REPLICATES,
        rng=seed,
    ).confidence_interval
    p_value = stats.permutation_test(
        (a, b),
        lambda x, y: float(np.mean(x - y)),
        permutation_type="samples",
        n_resamples=_PERMUTATIONS,
        rng=seed,
    ).pvalue
    return float(np.mean(diff)), float(interval.low), float(interval.high), float(p_value)
