"""Per-call LLM audit records: one ``llm_calls`` row plus one log event.

The log event uses OpenTelemetry GenAI semantic-convention attribute names
(``gen_ai.*``) so a dedicated tracing consumer can read the stream later
without renaming (ADR 0011).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from unstash.db.models import LlmCall, LlmCallOutcome

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


async def record_llm_call(  # noqa: PLR0913 — the audit record's fields
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    purpose: str,
    model: str,
    latency_ms: int,
    outcome: LlmCallOutcome,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost: float | None = None,
    error: str | None = None,
) -> None:
    """Write one ``llm_calls`` row and emit the matching log event.

    The session is expected to be org-scoped (request dependency or worker
    ``org_context``); the row commits with the caller's transaction, so a
    rolled-back operation does not leave a phantom cost record.
    """
    session.add(
        LlmCall(
            org_id=org_id,
            purpose=purpose,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            latency_ms=latency_ms,
            outcome=outcome,
            error=error,
        ),
    )
    logger.info(
        "llm_call",
        **{
            "gen_ai.operation.name": purpose,
            "gen_ai.request.model": model,
            "gen_ai.usage.input_tokens": prompt_tokens,
            "gen_ai.usage.output_tokens": completion_tokens,
        },
        org_id=str(org_id),
        cost=cost,
        latency_ms=latency_ms,
        outcome=outcome.value,
    )
