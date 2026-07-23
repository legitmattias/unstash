"""Cluster labeler — interface, fake, and factory (ADR 0009 / ADR 0011).

One call per cluster turns c-TF-IDF keywords and representative documents
into a human-readable category label. The hosted backend lives in
:mod:`unstash.inference.litellm_chat`; this module owns the interface, the
deterministic fake, and the backend selection.

Labeling is best-effort by contract: callers catch :class:`LabelError` and
keep the keyword-derived label.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from unstash.config import Settings

# A label is a short category name; anything longer is a failed generation.
MAX_LABEL_CHARS = 60


class LabelError(Exception):
    """The backend could not produce a usable label."""


@dataclass(frozen=True, slots=True)
class LabelRequest:
    """Data-minimal input for one cluster label."""

    keywords: Sequence[str]
    representative_titles: Sequence[str]
    representative_excerpts: Sequence[str]
    language: str


@dataclass(frozen=True, slots=True)
class LabelResult:
    """A generated label with the call's usage for the audit record."""

    label: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    cost: float | None


class ClusterLabeler(Protocol):
    """Interface every labeler backend implements."""

    async def label(self, request: LabelRequest) -> LabelResult:
        """Return a label for one cluster.

        Raises:
            LabelError: When no usable label could be produced.
        """
        ...


class FakeLabeler:
    """Deterministic labeler for tests: title-cases the top keyword."""

    async def label(self, request: LabelRequest) -> LabelResult:
        """Return a label derived from the request's first keyword."""
        if not request.keywords:
            msg = "no keywords to derive a label from"
            raise LabelError(msg)
        return LabelResult(
            label=request.keywords[0].capitalize(),
            model="fake",
            prompt_tokens=0,
            completion_tokens=0,
            cost=0.0,
        )


def get_labeler(settings: Settings) -> ClusterLabeler | None:
    """Select the labeler backend from configuration; ``None`` means off."""
    if settings.labeler_backend == "fake":
        return FakeLabeler()
    if settings.labeler_backend == "hosted":
        from unstash.inference.litellm_chat import LiteLlmLabeler  # noqa: PLC0415

        return LiteLlmLabeler(settings)
    return None
