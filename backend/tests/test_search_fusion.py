"""Unit tests for the weighted RRF fusion used by the search service."""

from __future__ import annotations

import uuid

from unstash.search.service import fuse_rrf

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_equal_weights_reward_agreement() -> None:
    scores = fuse_rrf([([A, B], 1.0), ([B, A], 1.0)], k=20)
    assert scores[A] == scores[B]
    assert scores[A] == 1.0 / 21 + 1.0 / 22


def test_vector_weight_dominates_on_disagreement() -> None:
    scores = fuse_rrf([([A, B], 3.0), ([B, A], 1.0)], k=20)
    assert scores[A] > scores[B]


def test_item_in_single_ranking_still_scores() -> None:
    scores = fuse_rrf([([A], 3.0), ([B], 1.0)], k=20)
    assert scores[A] == 3.0 / 21
    assert scores[B] == 1.0 / 21


def test_empty_rankings() -> None:
    assert fuse_rrf([([], 3.0), ([], 1.0)], k=20) == {}


def test_lower_rank_scores_less() -> None:
    scores = fuse_rrf([([A, B, C], 1.0)], k=20)
    assert scores[A] > scores[B] > scores[C]
