# bertopic, umap-learn, and hdbscan ship no type stubs.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Clustering engine — BERTopic over precomputed document embeddings.

Pure computation: takes one text and one embedding per document, returns
cluster assignments, c-TF-IDF keywords, representative documents, and a
silhouette score. No database access, no side effects — the service layer
owns persistence.

Embeddings arrive pooled to document level (mean of the document's chunk
embeddings) and are L2-normalized here before UMAP.

Parameters scale with corpus size: BERTopic's defaults (``min_topic_size``
10, ``n_neighbors`` 15) assume thousands of documents and collapse a
50-document corpus into one cluster plus noise. ``random_state`` is pinned,
so re-runs on identical input are identical.

Importing bertopic loads umap and triggers numba JIT compilation, so the
heavy imports are deferred into :func:`cluster_documents`; the API process
imports this module without paying that cost.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
from typing import TYPE_CHECKING, Any, cast

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy.typing as npt

_RANDOM_STATE = 42
_MIN_CLUSTER_FLOOR = 3
_DOCS_PER_MIN_CLUSTER = 15
_MAX_N_NEIGHBORS = 15
_MAX_UMAP_COMPONENTS = 5
_TOP_KEYWORDS = 10
_REPRESENTATIVES_PER_CLUSTER = 3
_LABEL_KEYWORDS = 3
_MIN_CLUSTERS_FOR_SILHOUETTE = 2
_STOPWORD_LANGUAGES = ("sv", "en")

# UMAP cannot build a neighbour graph on fewer documents.
MIN_DOCUMENTS = 10


@dataclasses.dataclass(frozen=True)
class ClusteringOutput:
    """Result of one clustering pass, index-aligned with the input."""

    assignments: list[int]
    """Cluster id per input document; ``-1`` is noise."""

    keywords: dict[int, list[tuple[str, float]]]
    """Top c-TF-IDF terms per cluster id, best first."""

    representatives: dict[int, list[int]]
    """Input indices closest to each cluster's centroid, best first."""

    silhouette: float | None
    """Silhouette over non-noise documents in the reduced space; ``None``
    when undefined (fewer than two clusters)."""

    params: dict[str, Any]
    """Parameter snapshot for ``clustering_runs.params``."""


def keyword_label(keywords: Sequence[tuple[str, float]]) -> str:
    """Build the placeholder label from a cluster's top keywords."""
    return ", ".join(term for term, _score in keywords[:_LABEL_KEYWORDS])


def cluster_documents(
    texts: Sequence[str],
    embeddings: npt.NDArray[np.float64],
) -> ClusteringOutput:
    """Cluster documents from pooled embeddings and representative texts.

    Args:
        texts: One text per document (title + leading content), used only
            for c-TF-IDF keyword extraction, not for embedding.
        embeddings: Array of shape ``(len(texts), dim)`` — pooled
            document-level embeddings.

    Returns:
        A :class:`ClusteringOutput` aligned with the input order.

    Raises:
        ValueError: If inputs disagree in length or the corpus is smaller
            than :data:`MIN_DOCUMENTS`.
    """
    import stopwordsiso  # noqa: PLC0415
    from bertopic import BERTopic  # noqa: PLC0415
    from bertopic.vectorizers import ClassTfidfTransformer  # noqa: PLC0415
    from hdbscan import HDBSCAN  # noqa: PLC0415
    from sklearn.feature_extraction.text import CountVectorizer  # noqa: PLC0415
    from sklearn.metrics import silhouette_score  # noqa: PLC0415
    from umap import UMAP  # noqa: PLC0415

    n = len(texts)
    if embeddings.shape[0] != n:
        msg = f"texts ({n}) and embeddings ({embeddings.shape[0]}) disagree in length"
        raise ValueError(msg)
    if n < MIN_DOCUMENTS:
        msg = f"clustering needs at least {MIN_DOCUMENTS} documents, got {n}"
        raise ValueError(msg)

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / np.maximum(norms, 1e-12)

    min_cluster_size = max(_MIN_CLUSTER_FLOOR, n // _DOCS_PER_MIN_CLUSTER)
    n_neighbors = min(_MAX_N_NEIGHBORS, max(2, n // 5))
    n_components = min(_MAX_UMAP_COMPONENTS, n - 2)

    umap_model = UMAP(
        n_neighbors=n_neighbors,
        n_components=n_components,
        min_dist=0.0,
        metric="cosine",
        random_state=_RANDOM_STATE,
    )
    hdbscan_model = HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    # Function words dominate c-TF-IDF on small corpora (few clusters give
    # the class-IDF little to discount), so keywords need an explicit
    # stop-word list; reduce_frequent_words dampens the corpus-common
    # remainder.
    stop_words = sorted(stopwordsiso.stopwords(_STOPWORD_LANGUAGES))
    # language="english" (the default) strips all non-ASCII characters in
    # BERTopic's c-TF-IDF preprocessing; "multilingual" preserves å/ä/ö.
    # With embedding_model=None the setting affects only that preprocessing.
    model = BERTopic(
        language="multilingual",
        embedding_model=None,
        umap_model=umap_model,
        hdbscan_model=hdbscan_model,
        vectorizer_model=CountVectorizer(stop_words=stop_words),
        ctfidf_model=ClassTfidfTransformer(reduce_frequent_words=True),
        top_n_words=_TOP_KEYWORDS,
        calculate_probabilities=False,
        verbose=False,
    )
    topics, _probs = model.fit_transform(list(texts), embeddings=normalized)
    assignments = [int(t) for t in topics]
    labels = np.array(assignments)

    keywords: dict[int, list[tuple[str, float]]] = {}
    representatives: dict[int, list[int]] = {}
    for cluster_id in sorted({a for a in assignments if a != -1}):
        # get_topic returns False for an unknown topic id.
        raw_terms = cast(
            "list[tuple[str, float]]",
            model.get_topic(cluster_id) or [],
        )
        keywords[cluster_id] = [(str(term), float(score)) for term, score in raw_terms if str(term)]

        member_idx = np.flatnonzero(labels == cluster_id)
        centroid = normalized[member_idx].mean(axis=0)
        centroid = centroid / max(float(np.linalg.norm(centroid)), 1e-12)
        similarity = normalized[member_idx] @ centroid
        best_first = member_idx[np.argsort(-similarity)]
        representatives[cluster_id] = [int(i) for i in best_first[:_REPRESENTATIVES_PER_CLUSTER]]

    # Silhouette is computed in the UMAP-reduced space, where HDBSCAN
    # clustered, over non-noise documents; it is undefined for fewer than
    # two clusters or when every non-noise document is a centroid.
    silhouette: float | None = None
    reduced = np.asarray(umap_model.embedding_)
    non_noise = labels != -1
    if len(keywords) >= _MIN_CLUSTERS_FOR_SILHOUETTE and int(non_noise.sum()) > len(keywords):
        silhouette = float(silhouette_score(reduced[non_noise], labels[non_noise]))

    params: dict[str, Any] = {
        "n_documents": n,
        "min_cluster_size": min_cluster_size,
        "n_neighbors": n_neighbors,
        "n_components": n_components,
        "random_state": _RANDOM_STATE,
        "umap_metric": "cosine",
        "silhouette_space": "umap",
        "stopword_languages": list(_STOPWORD_LANGUAGES),
        "reduce_frequent_words": True,
        "bertopic_version": importlib.metadata.version("bertopic"),
    }
    return ClusteringOutput(
        assignments=assignments,
        keywords=keywords,
        representatives=representatives,
        silhouette=silhouette,
        params=params,
    )
