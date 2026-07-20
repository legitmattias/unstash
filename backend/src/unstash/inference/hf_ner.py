"""Hosted NER via a HuggingFace Inference Endpoint (token classification).

Implements the ``EntityExtractor`` protocol against an EU-region, scale-to-zero
endpoint running a token-classification model. Everything HF-specific lives
here — request shape, response parsing, retries; the category mapping and
filtering are shared with the local backend in ``documents.ner``.

Failure is best-effort per ADR 0009: :meth:`extract` raises ``NerError`` and
the caller (ingest) logs a warning and proceeds with the document.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from unstash.documents.ner import ExtractedEntity, entities_from_tagged

# A scale-to-zero endpoint returns 503 while the replica cold-starts; retry
# through that in addition to the usual transient statuses.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 4


class NerError(RuntimeError):
    """Raised when the hosted NER endpoint cannot produce a result."""


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff: 1s, 2s, 4s, ... — generous for cold starts."""
    return 1.0 * (2**attempt)


class HfEndpointExtractor:
    """Calls a HuggingFace token-classification Inference Endpoint."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        api_key: str,
        min_score: float,
        timeout: float,
    ) -> None:
        """Configure the endpoint; no network happens until :meth:`extract`."""
        self._url = endpoint_url
        self._api_key = api_key
        self._min_score = min_score
        self._timeout = timeout

    async def extract(self, text: str) -> list[ExtractedEntity]:
        """Return the entities the endpoint tags in ``text``."""
        if not text.strip():
            return []
        if not self._url:
            msg = "NER endpoint URL is not configured."
            raise NerError(msg)

        payload: dict[str, object] = {
            "inputs": text,
            "parameters": {"aggregation_strategy": "first"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        response = await self._post_with_retries(payload, headers)

        try:
            raw: Any = response.json()
        except ValueError as exc:
            msg = f"Unexpected NER endpoint response: {exc}"
            raise NerError(msg) from exc
        if not isinstance(raw, list):
            msg = f"NER endpoint returned {type(raw).__name__}, expected a list"
            raise NerError(msg)
        results: list[dict[str, Any]] = raw
        return entities_from_tagged(results, self._min_score)

    async def _post_with_retries(
        self,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> httpx.Response:
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = await client.post(self._url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    last_error = exc
                else:
                    if response.is_success:
                        return response
                    if response.status_code not in _RETRYABLE_STATUS:
                        detail = response.text[:300]
                        msg = f"NER endpoint returned {response.status_code}: {detail}"
                        raise NerError(msg)
                    last_error = NerError(f"NER endpoint returned retryable {response.status_code}")
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_backoff_seconds(attempt))

        msg = f"NER endpoint failed after {_MAX_RETRIES + 1} attempts: {last_error}"
        raise NerError(msg) from last_error
