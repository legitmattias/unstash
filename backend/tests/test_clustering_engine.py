"""Clustering engine tests on synthetic embeddings with planted structure.

The engine is exercised with Gaussian blobs whose ground truth is known, so
assertions are exact: recovered clusters must align with the planted ones,
re-runs must be identical, and keywords must reflect each blob's vocabulary.
"""

from __future__ import annotations

import numpy as np
import pytest
from sklearn.metrics import adjusted_rand_score

from unstash.clustering.engine import (
    MIN_DOCUMENTS,
    cluster_documents,
    keyword_label,
)

_DIM = 64
_DOCS_PER_BLOB = 15

# Distinct vocabulary per planted blob, so c-TF-IDF keywords are checkable.
_BLOB_VOCAB = (
    ("protokoll", "styrelse", "beslut"),
    ("faktura", "belopp", "betalning"),
    ("avtal", "leverantör", "uppsägning"),
    ("stämma", "motion", "röstning"),
)


def _planted_corpus(
    seed: int = 7,
) -> tuple[list[str], np.ndarray, list[int]]:
    """Build texts + embeddings for four well-separated Gaussian blobs."""
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(len(_BLOB_VOCAB), _DIM)) * 10.0

    texts: list[str] = []
    embeddings: list[np.ndarray] = []
    truth: list[int] = []
    for blob, (center, vocab) in enumerate(zip(centers, _BLOB_VOCAB, strict=True)):
        for i in range(_DOCS_PER_BLOB):
            texts.append(f"{vocab[0]} {vocab[1]} {vocab[2]} dokument {i}")
            embeddings.append(center + rng.normal(size=_DIM))
            truth.append(blob)
    return texts, np.array(embeddings), truth


def test_recovers_planted_blobs():
    texts, embeddings, truth = _planted_corpus()
    result = cluster_documents(texts, embeddings)

    found = {a for a in result.assignments if a != -1}
    assert len(found) == len(_BLOB_VOCAB)
    # Noise assignments can pull ARI below 1.0 on otherwise clean recovery.
    assert adjusted_rand_score(truth, result.assignments) > 0.9


def test_rerun_is_identical():
    texts, embeddings, _ = _planted_corpus()
    first = cluster_documents(texts, embeddings)
    second = cluster_documents(texts, embeddings)

    assert first.assignments == second.assignments
    assert first.keywords == second.keywords
    assert first.silhouette == second.silhouette


def test_keywords_reflect_blob_vocabulary():
    texts, embeddings, truth = _planted_corpus()
    result = cluster_documents(texts, embeddings)

    for cluster_id, terms in result.keywords.items():
        member = result.assignments.index(cluster_id)
        vocab = _BLOB_VOCAB[truth[member]]
        top_terms = {term for term, _ in terms[:5]}
        assert top_terms & set(vocab), (
            f"cluster {cluster_id} keywords {top_terms} miss blob vocab {vocab}"
        )


def test_representatives_belong_to_their_cluster():
    texts, embeddings, _ = _planted_corpus()
    result = cluster_documents(texts, embeddings)

    for cluster_id, indices in result.representatives.items():
        assert 1 <= len(indices) <= 3
        for i in indices:
            assert result.assignments[i] == cluster_id


def test_silhouette_and_params_reported():
    texts, embeddings, _ = _planted_corpus()
    result = cluster_documents(texts, embeddings)

    assert result.silhouette is not None
    assert result.silhouette > 0.5  # planted blobs are well separated
    assert result.params["n_documents"] == len(texts)
    assert result.params["silhouette_space"] == "umap"
    assert "bertopic_version" in result.params


def test_rejects_too_few_documents():
    texts, embeddings, _ = _planted_corpus()
    with pytest.raises(ValueError, match="at least"):
        cluster_documents(texts[: MIN_DOCUMENTS - 1], embeddings[: MIN_DOCUMENTS - 1])


def test_rejects_length_mismatch():
    texts, embeddings, _ = _planted_corpus()
    with pytest.raises(ValueError, match="disagree"):
        cluster_documents(texts[:-1], embeddings)


def test_keyword_label_joins_top_terms():
    label = keyword_label([("avtal", 0.9), ("leverantör", 0.5), ("uppsägning", 0.3), ("x", 0.1)])
    assert label == "avtal, leverantör, uppsägning"
