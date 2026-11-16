"""Upload planned files to the document API, one request per file."""

from __future__ import annotations

import enum
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

    from unstash.importer.plan import ImportItem

_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 2.0
_MAX_BACKOFF_SECONDS = 120.0

_HTTP_OK = 200
_HTTP_CREATED = 201
_HTTP_CONTENT_TOO_LARGE = 413
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR = 500


class Outcome(enum.StrEnum):
    """Result of importing one file."""

    CREATED = "created"
    DUPLICATE = "duplicate"
    TOO_LARGE = "too_large"
    REJECTED = "rejected"
    FAILED = "failed"


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Seconds to wait before the next attempt.

    A numeric ``Retry-After`` header takes precedence; otherwise the
    delay doubles per attempt. Both are capped.
    """
    if response is not None:
        header = response.headers.get("Retry-After", "")
        if header.isdigit():
            return min(float(header), _MAX_BACKOFF_SECONDS)
    return min(_BACKOFF_BASE_SECONDS * 2**attempt, _MAX_BACKOFF_SECONDS)


def upload_item(
    client: httpx.Client,
    org_slug: str,
    item: ImportItem,
    sleep: Callable[[float], None] = time.sleep,
) -> Outcome:
    """Upload one file and classify the response.

    Rate-limit responses, server errors and transport errors are retried
    up to a fixed number of attempts. Any other client error is final,
    because repeating the same request would get the same answer.
    """
    for attempt in range(_MAX_ATTEMPTS):
        response: httpx.Response | None = None
        try:
            with item.file.open("rb") as handle:
                response = client.post(
                    f"/api/orgs/{org_slug}/documents",
                    files={"file": (item.file.name, handle, item.mime_type)},
                    data={
                        "source_path": item.source_path,
                        "source_modified_at": item.modified_at.isoformat(),
                    },
                )
        except httpx.TransportError:
            response = None

        if response is not None:
            code = response.status_code
            if code == _HTTP_CREATED:
                return Outcome.CREATED
            if code == _HTTP_OK:
                return Outcome.DUPLICATE
            if code == _HTTP_CONTENT_TOO_LARGE:
                return Outcome.TOO_LARGE
            if code != _HTTP_TOO_MANY_REQUESTS and code < _HTTP_SERVER_ERROR:
                return Outcome.REJECTED

        if attempt < _MAX_ATTEMPTS - 1:
            sleep(_retry_delay(response, attempt))
    return Outcome.FAILED
