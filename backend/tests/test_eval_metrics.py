"""Tests for the retrieval-eval ranking metrics."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "retrieval"))

from metrics import (
    clustered_bootstrap_ci,
    clustered_paired_test,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

JUDGMENTS = {"a": 3, "b": 2, "c": 1}  # a, b relevant; c contextual only


def test_recall_counts_grade_two_and_up() -> None:
    assert recall_at_k(["a", "x", "b"], JUDGMENTS, k=3) == 1.0
    assert recall_at_k(["a", "x", "y"], JUDGMENTS, k=3) == 0.5
    assert recall_at_k(["c", "x", "y"], JUDGMENTS, k=3) == 0.0  # grade 1 not relevant


def test_recall_respects_k() -> None:
    assert recall_at_k(["x", "a", "b"], JUDGMENTS, k=1) == 0.0
    assert recall_at_k(["x", "a", "b"], JUDGMENTS, k=2) == 0.5


def test_recall_undefined_without_relevant_docs() -> None:
    with pytest.raises(ValueError, match="undefined"):
        recall_at_k(["a"], {"a": 1}, k=1)


def test_mrr_first_relevant_position() -> None:
    assert mrr(["a", "b"], JUDGMENTS) == 1.0
    assert mrr(["x", "b", "a"], JUDGMENTS) == 0.5
    assert mrr(["c", "x", "b"], JUDGMENTS) == pytest.approx(1 / 3)
    assert mrr(["x", "y"], JUDGMENTS) == 0.0


def test_ndcg_perfect_ordering_is_one() -> None:
    assert ndcg_at_k(["a", "b", "c"], JUDGMENTS, k=3) == pytest.approx(1.0)


def test_ndcg_penalises_late_answer() -> None:
    perfect = ndcg_at_k(["a", "b", "c"], JUDGMENTS, k=3)
    late = ndcg_at_k(["c", "b", "a"], JUDGMENTS, k=3)
    assert late < perfect


def test_ndcg_hand_computed_value() -> None:
    # ranked: b (grade 2) then a (grade 3); ideal: a then b.
    got = ndcg_at_k(["b", "a"], JUDGMENTS, k=2)
    dcg = (2**2 - 1) / math.log2(2) + (2**3 - 1) / math.log2(3)
    idcg = (2**3 - 1) / math.log2(2) + (2**2 - 1) / math.log2(3)
    assert got == pytest.approx(dcg / idcg)


def test_ndcg_zero_when_nothing_judged() -> None:
    assert ndcg_at_k(["x"], {}, k=10) == 0.0


def test_clustered_bootstrap_ci_brackets_the_query_level_mean() -> None:
    values = [0.8, 0.6, 0.7, 0.9, 0.5]
    clusters = ["d1", "d2", "d3", "d4", "d5"]
    mean, low, high = clustered_bootstrap_ci(values, clusters)
    assert low <= mean <= high
    # Reported mean is the query-level mean, matching the headline figure.
    assert math.isclose(mean, sum(values) / len(values))


def test_clustered_bootstrap_ci_is_query_level_not_document_mean() -> None:
    # d1 carries three queries, d2 one. Query-level mean (0.7) differs from the
    # mean of document-means (0.5) — the bug the review caught.
    values = [0.9, 0.9, 0.9, 0.1]
    clusters = ["d1", "d1", "d1", "d2"]
    mean, low, high = clustered_bootstrap_ci(values, clusters)
    assert math.isclose(mean, 0.7)
    assert low <= mean <= high


def test_clustered_bootstrap_ci_degenerate_single_cluster_no_nan() -> None:
    mean, low, high = clustered_bootstrap_ci([0.4, 0.6], ["d1", "d1"])
    assert math.isclose(mean, 0.5)
    # One cluster resamples to itself every time — zero spread, never NaN.
    assert math.isclose(low, 0.5)
    assert math.isclose(high, 0.5)
    assert not math.isnan(low)
    assert not math.isnan(high)


def test_clustering_widens_the_interval() -> None:
    # Within-document correlation (d1 all high, d2 all low) means fewer effective
    # units than treating each query as independent — so the clustered CI is wider.
    values = [0.9, 0.9, 0.9, 0.1, 0.1, 0.1]
    independent = ["a", "b", "c", "d", "e", "f"]
    clustered = ["d1", "d1", "d1", "d2", "d2", "d2"]
    _, low_i, high_i = clustered_bootstrap_ci(values, independent)
    _, low_c, high_c = clustered_bootstrap_ci(values, clustered)
    assert (high_c - low_c) >= (high_i - low_i)


def test_clustered_paired_test_three_document_permutation_floor() -> None:
    # A consistent +0.1 over three documents: the two-sided permutation p cannot
    # drop below ~0.25 (2 of 2**3 sign-flips), so it refuses to over-claim.
    a = [0.8, 0.6, 0.7]
    b = [0.7, 0.5, 0.6]
    clusters = ["d1", "d2", "d3"]
    mean, low, high, p = clustered_paired_test(a, b, clusters)
    assert math.isclose(mean, 0.1, abs_tol=1e-9)
    assert low <= mean <= high
    assert p == pytest.approx(0.25, abs=0.01)


def test_clustered_paired_test_pvalue_in_unit_interval() -> None:
    a = [0.50, 0.55, 0.60, 0.45]
    b = [0.50, 0.54, 0.61, 0.46]
    clusters = ["d1", "d2", "d3", "d4"]
    _, _, _, p = clustered_paired_test(a, b, clusters)
    assert 0.0 < p <= 1.0
