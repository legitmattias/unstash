"""Model execution for the inference service."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from unstash.inference_server.settings import InferenceServerSettings


class Engine(Protocol):
    """Runs the embedding and reranking models."""

    def embed(self, texts: list[str], *, is_query: bool) -> tuple[list[list[float]], int]:
        """Return one L2-normalised vector per text, and the token count."""
        ...

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return one relevance score per document, in input order."""
        ...


class SentenceTransformerEngine:
    """Runs both models in-process on CPU.

    Inference is serialised with a lock: each call already uses every
    configured thread, so concurrent calls would only contend.
    """

    def __init__(self, settings: InferenceServerSettings) -> None:
        """Load both models. Downloads the weights on first use."""
        # Imported here: importing torch costs seconds and hundreds of MB,
        # which only the serving process should pay.
        import torch  # noqa: PLC0415
        from sentence_transformers import CrossEncoder, SentenceTransformer  # noqa: PLC0415

        torch.set_num_threads(settings.threads)
        self._settings = settings
        self._embedder = SentenceTransformer(
            settings.embedding_model,
            revision=settings.embedding_revision,
            device="cpu",
        )
        self._embedder.max_seq_length = settings.embedding_max_tokens
        self._reranker = CrossEncoder(
            settings.rerank_model,
            revision=settings.rerank_revision,
            device="cpu",
            max_length=settings.rerank_max_tokens,
        )
        self._lock = threading.Lock()

    def embed(self, texts: list[str], *, is_query: bool) -> tuple[list[list[float]], int]:
        """Embed ``texts``; queries use the model's query prompt when it has one."""
        prompt_name = self._settings.embedding_query_prompt if is_query else None
        with self._lock:
            encoded = self._embedder.tokenizer(
                texts,
                truncation=True,
                max_length=self._settings.embedding_max_tokens,
            )
            # sentence-transformers' overloads are only partially typed.
            vectors = self._embedder.encode(  # pyright: ignore[reportUnknownMemberType]
                texts,
                prompt_name=prompt_name,
                batch_size=self._settings.embedding_batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        total_tokens = sum(len(ids) for ids in encoded["input_ids"])
        return vectors.tolist(), total_tokens

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Score ``documents`` against ``query``."""
        # Scored in length order so each small batch pads to similar
        # lengths; scores are returned in input order.
        by_length = sorted(range(len(documents)), key=lambda i: len(documents[i]))
        pairs = [[query, documents[i]] for i in by_length]
        with self._lock:
            raw = self._reranker.predict(  # pyright: ignore[reportUnknownMemberType]
                pairs,
                batch_size=self._settings.rerank_batch_size,
                show_progress_bar=False,
            )
        scores = [0.0] * len(documents)
        for position, index in enumerate(by_length):
            scores[index] = float(raw[position])
        return scores
