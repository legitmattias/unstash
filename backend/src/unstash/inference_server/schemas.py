"""Request and response bodies of the inference service."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

# Bounds one request's work. Texts longer than the model's token limit
# are truncated by the model; the character cap only bounds the payload.
MAX_TEXTS = 64
MAX_DOCUMENTS = 100
MAX_TEXT_CHARS = 50_000
MAX_QUERY_CHARS = 2_000

Text = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT_CHARS)]


class EmbedRequest(BaseModel):
    """Texts to embed, as passages for indexing or as search queries."""

    texts: list[Text] = Field(min_length=1, max_length=MAX_TEXTS)
    task: Literal["passage", "query"]


class EmbedResponse(BaseModel):
    """One vector per input text, in input order."""

    vectors: list[list[float]]
    total_tokens: int


class RerankRequest(BaseModel):
    """A query and the candidate documents to score against it."""

    query: Annotated[str, StringConstraints(min_length=1, max_length=MAX_QUERY_CHARS)]
    documents: list[Text] = Field(min_length=1, max_length=MAX_DOCUMENTS)


class RerankResponse(BaseModel):
    """One relevance score per document, in input order."""

    scores: list[float]
