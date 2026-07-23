# litellm ships no type stubs.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Hosted cluster labeling via the in-process LiteLLM SDK (ADR 0011).

One chat completion per cluster against the configured model (EU-hosted
provider), JSON mode, with usage and cost read from the response for the
``llm_calls`` audit record. The litellm import is deferred to first use —
the library is heavy and only the worker's labeling path needs it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from unstash.clustering.labeler import (
    MAX_LABEL_CHARS,
    LabelError,
    LabelResult,
)

if TYPE_CHECKING:
    from unstash.clustering.labeler import LabelRequest
    from unstash.config import Settings

_LANGUAGE_NAMES = {"sv": "Swedish", "en": "English"}

_SYSTEM_PROMPT = (
    "You name document categories for an archive. Given keywords and sample"
    " documents from one category, answer with a JSON object"
    ' {"label": "..."} where label is a concise category name of one to'
    " three words in {language}. Use the archive's own vocabulary; do not"
    " invent information."
)


def _user_prompt(request: LabelRequest) -> str:
    lines = ["Keywords: " + ", ".join(request.keywords)]
    for title, excerpt in zip(
        request.representative_titles,
        request.representative_excerpts,
        strict=True,
    ):
        lines.append(f"- {title}: {excerpt}")
    return "\n".join(lines)


class LiteLlmLabeler:
    """Labeler backend calling the configured chat model through litellm."""

    def __init__(self, settings: Settings) -> None:
        """Capture the model, credentials, and timeout from settings."""
        self._model = settings.labeler_model
        self._api_key = settings.mistral_api_key
        self._timeout = settings.labeler_timeout_seconds

    async def label(self, request: LabelRequest) -> LabelResult:
        """Generate one cluster label; usage travels back for the audit row."""
        import litellm  # noqa: PLC0415

        language = _LANGUAGE_NAMES.get(request.language, request.language)
        # Non-streaming call; the union with the streaming wrapper in the
        # SDK's return annotation does not apply.
        response: Any
        try:
            response = await litellm.acompletion(
                model=self._model,
                api_key=self._api_key,
                timeout=self._timeout,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": _SYSTEM_PROMPT.replace("{language}", language),
                    },
                    {"role": "user", "content": _user_prompt(request)},
                ],
            )
        except Exception as exc:
            msg = f"labeling call failed: {type(exc).__name__}"
            raise LabelError(msg) from exc

        content = response.choices[0].message.content
        try:
            parsed: dict[str, Any] = json.loads(content or "")
            label = str(parsed["label"]).strip()
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            msg = "labeling response was not the expected JSON object"
            raise LabelError(msg) from exc
        if not label or len(label) > MAX_LABEL_CHARS:
            msg = f"label empty or over {MAX_LABEL_CHARS} chars"
            raise LabelError(msg)

        usage = getattr(response, "usage", None)
        try:
            cost = float(litellm.completion_cost(completion_response=response))
        except Exception:
            cost = None
        return LabelResult(
            label=label,
            model=self._model,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            cost=cost,
        )
