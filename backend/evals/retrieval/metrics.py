"""Ranking metrics for the retrieval evaluation harness.

Pure functions over ranked document lists and graded judgments.
Conventions (see evals/README.md): grades are 0-3; Recall@k and MRR
treat grade >= 2 as relevant; nDCG uses the graded scale.
"""

from __future__ import annotations

import math

RELEVANT_GRADE = 2


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
