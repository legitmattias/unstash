"""Configuration of the inference service.

Read from ``UNSTASH_INFERENCE_``-prefixed environment variables. Kept
apart from the application settings: this process needs no database,
broker or secret configuration.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class InferenceServerSettings(BaseSettings):
    """Models and limits for the inference service."""

    model_config = SettingsConfigDict(
        env_prefix="UNSTASH_INFERENCE_",
        case_sensitive=False,
        extra="ignore",
    )

    # Revisions are pinned so a deploy never picks up changed weights.
    embedding_model: str = Field(default="BAAI/bge-m3")
    embedding_revision: str = Field(default="5617a9f61b028005a4858fdac845db406aefb181")
    embedding_query_prompt: str | None = Field(
        default=None,
        description="Name of the model's query prompt, for models that define one.",
    )
    embedding_max_tokens: int = Field(default=1024, gt=0)
    embedding_batch_size: int = Field(default=8, gt=0)

    rerank_model: str = Field(default="BAAI/bge-reranker-v2-m3")
    rerank_revision: str = Field(default="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
    rerank_max_tokens: int = Field(
        default=512,
        gt=0,
        description="Token limit of one query-document pair.",
    )
    rerank_batch_size: int = Field(default=4, gt=0)

    threads: int = Field(
        default=4,
        gt=0,
        description="CPU threads used for inference. Match the container CPU limit.",
    )
