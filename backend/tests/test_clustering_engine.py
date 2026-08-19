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


@pytest.mark.stochastic
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


def test_keywords_exclude_stop_words_and_keep_diacritics():
    # Realistic prose: function words dominate raw term frequency in every
    # blob, and the content words carry å/ä/ö.
    rng_texts = {
        0: "protokollet fördes av ordföranden och styrelsen beslutade att under året",
        1: "fakturan som avser beloppet ska betalas under månaden och förfaller",
        2: "avtalet med leverantören är uppsagt och skall omförhandlas under våren",
        3: "stämman höll omröstning om motionen och medlemmarna röstade under mötet",
    }
    texts, embeddings, _ = _planted_corpus()
    texts = [f"{rng_texts[i % 4]} {t}" for i, t in enumerate(texts)]

    result = cluster_documents(texts, embeddings)

    all_terms = {term for terms in result.keywords.values() for term, _ in terms}
    banned = {"och", "att", "av", "som", "under", "är", "den", "the", "and"}
    assert not (all_terms & banned), f"stop words leaked into keywords: {all_terms & banned}"
    # Diacritics survive tokenization end-to-end.
    assert any("å" in t or "ä" in t or "ö" in t for t in all_terms), all_terms


@pytest.mark.stochastic
def test_leaf_selection_recovers_planted_blobs_too():
    texts, embeddings, truth = _planted_corpus()
    result = cluster_documents(texts, embeddings, cluster_selection_method="leaf")

    assert result.params["cluster_selection_method"] == "leaf"
    assert adjusted_rand_score(truth, result.assignments) > 0.9


@pytest.mark.stochastic
def test_duplicate_batch_stays_one_cluster():
    # Batches of (near-)identical documents (same form, one per unit) are a
    # real archive pattern; more duplicates than n_neighbors is a documented
    # UMAP hazard, handled with unique=True. Regression guard: the batch
    # must stay together and must not land in noise.
    texts, embeddings, truth = _planted_corpus()
    rng = np.random.default_rng(3)
    form_vector = rng.normal(size=_DIM) * 10.0
    n_forms = 17
    form_texts = [f"energideklaration form {i}" for i in range(n_forms)]
    # Half exact duplicates, half near-duplicates — both occur in practice.
    jitter = rng.normal(size=(n_forms, _DIM)) * 1e-4
    jitter[: n_forms // 2] = 0.0
    form_embeddings = form_vector + jitter

    all_texts = texts + form_texts
    all_embeddings = np.vstack([embeddings, form_embeddings])
    result = cluster_documents(all_texts, all_embeddings)

    form_assignments = result.assignments[len(texts) :]
    assert len(set(form_assignments)) == 1, "duplicate batch split across clusters"
    assert form_assignments[0] != -1, "duplicate batch fell out as noise"
    # The original planted structure is unaffected by the extra batch.
    assert adjusted_rand_score(truth, result.assignments[: len(texts)]) > 0.9


def test_min_cluster_size_is_capped_for_large_corpora():
    texts, embeddings, _ = _planted_corpus()
    # Inflate to ~180 docs by jittered copies that stay distinct (> duplicate
    # threshold apart) so the cap, not the collapse, is exercised.
    rng = np.random.default_rng(5)
    big_texts = list(texts)
    extra = []
    for i in range(120):
        base = embeddings[i % len(texts)]
        extra.append(base + rng.normal(size=_DIM) * 2.0)
        big_texts.append(f"{texts[i % len(texts)]} kopia {i}")
    big_embeddings = np.vstack([embeddings, np.array(extra)])

    result = cluster_documents(big_texts, big_embeddings)
    assert result.params["min_cluster_size"] <= 8
    assert result.params["min_samples"] <= 5
