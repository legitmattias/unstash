"""Real model execution in the inference engine, with tiny test models.

The production models are several GB; these are a few MB and exercise
the same sentence-transformers code paths, so a library upgrade that
changes ``encode`` or ``predict`` surfaces here.
"""

from __future__ import annotations

import math

import pytest

from unstash.inference_server.engine import SentenceTransformerEngine
from unstash.inference_server.settings import InferenceServerSettings


@pytest.fixture(scope="module")
def engine() -> SentenceTransformerEngine:
    return SentenceTransformerEngine(
        InferenceServerSettings(
            embedding_model="sentence-transformers-testing/stsb-bert-tiny-safetensors",
            embedding_revision="f3cb857cba53019a20df283396bcca179cf051a4",
            embedding_max_tokens=64,
            rerank_model="cross-encoder-testing/reranker-bert-tiny-gooaq-bce",
            rerank_revision="0d75677beaddce2ff048cbd96079c3e2ce0e984f",
            rerank_max_tokens=64,
            rerank_batch_size=2,
            threads=1,
        ),
    )


def test_embed_returns_normalised_vectors_and_token_count(
    engine: SentenceTransformerEngine,
) -> None:
    texts = ["a short text", "another text that is somewhat longer than the first"]
    vectors, total_tokens = engine.embed(texts, is_query=False)

    assert len(vectors) == len(texts)
    assert len({len(vector) for vector in vectors}) == 1
    for vector in vectors:
        assert math.isclose(sum(value * value for value in vector), 1.0, abs_tol=1e-4)
    assert total_tokens > len(texts)


def test_embed_is_deterministic_for_queries(engine: SentenceTransformerEngine) -> None:
    first, _ = engine.embed(["what is this"], is_query=True)
    second, _ = engine.embed(["what is this"], is_query=True)
    assert first == second


def test_embed_truncates_long_input(engine: SentenceTransformerEngine) -> None:
    _, total_tokens = engine.embed(["word " * 500], is_query=False)
    assert total_tokens == 64


def test_rerank_scores_follow_input_order(engine: SentenceTransformerEngine) -> None:
    documents = ["x" * 40, "short", "a document of medium length"]
    scores = engine.rerank("query", documents)
    assert len(scores) == len(documents)

    # The engine scores in length order; each score must still belong to
    # the document at the same input position.
    for index, document in enumerate(documents):
        assert math.isclose(engine.rerank("query", [document])[0], scores[index], abs_tol=1e-4)
