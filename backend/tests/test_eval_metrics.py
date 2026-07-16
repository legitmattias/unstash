"""Tests for the retrieval-eval ranking metrics."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "retrieval"))

from metrics import mrr, ndcg_at_k, recall_at_k

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
