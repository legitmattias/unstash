"""POST-with-retries plumbing shared by the HTTP inference clients."""

from __future__ import annotations

import asyncio

import httpx

# Transient HTTP statuses worth retrying; other non-2xx fail immediately.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff: 0.5s, 1s, 2s, ..."""
    return 0.5 * (2**attempt)


class RetryingPoster:
    """POSTs JSON to one URL, retrying transient failures with backoff.

    Accepts an optional injected ``httpx.AsyncClient`` so a process can
    share one connection pool across requests; the caller owns its
    lifecycle.
    """

    def __init__(  # noqa: PLR0913 — transport config; grouping into an object buys nothing
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout: float,
        max_retries: int,
        http_client: httpx.AsyncClient | None,
        error_cls: type[Exception],
        label: str,
    ) -> None:
        self._headers = headers
        self._url = url
        self._timeout = timeout
        self._max_retries = max_retries
        self._http_client = http_client
        self._error_cls = error_cls
        self._label = label

    async def post(self, payload: dict[str, object]) -> httpx.Response:
        headers = self._headers
        if self._http_client is not None:
            return await self._attempt_loop(self._http_client, payload, headers)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await self._attempt_loop(client, payload, headers)

    async def _attempt_loop(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.post(
                    self._url,
                    json=payload,
                    headers=headers,
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.is_success:
                    return response
                if response.status_code not in _RETRYABLE_STATUS:
                    detail = response.text[:300]
                    msg = f"{self._label} returned {response.status_code}: {detail}"
                    raise self._error_cls(msg)
                last_error = self._error_cls(
                    f"{self._label} returned retryable {response.status_code}",
                )
            if attempt < self._max_retries:
                await asyncio.sleep(_backoff_seconds(attempt))

        msg = f"{self._label} request failed after {self._max_retries + 1} attempts: {last_error}"
        raise self._error_cls(msg) from last_error
