"""LlmCall model — one row per LLM API call (ADR 0011).

Rows are immutable audit records: which component called which model for
which org, the token usage and cost, and the outcome. Per-org cost is a
query over this table, keeping the LLM budget measured rather than
estimated.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    Uuid,
    text,
)
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Mapped, mapped_column

from unstash.db.models.base import Base


class LlmCallOutcome(enum.StrEnum):
    """How an LLM call ended."""

    OK = "ok"
    ERROR = "error"


class LlmCall(Base):
    """One LLM API call, scoped to one organisation."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('ok', 'error')",
            name="llm_call_outcome_valid",
        ),
        # Per-org cost aggregation over time; leads with org_id to match
        # the RLS predicate.
        Index("ix_llm_calls_org_id_created_at", "org_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The calling component's stable identifier, e.g. "cluster_label".
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # EUR. Numeric, not float — costs are summed across many rows.
    cost: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa_func.now(),
        nullable=False,
    )
