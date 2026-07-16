"""Scanned-document OCR via the Mistral OCR API (hosted backend, ADR 0009).

Triggered when a PDF parses with suspiciously little text per page (a
scan has no text layer). The document is sent as a base64 data URI —
private files never need a public URL — and comes back as per-page
markdown, which is then chunked through the normal parsing path.

The local alternative (OCR engine inside the parsing library) remains
the self-hosting option per ADR 0009; the trigger and call sites are
backend-agnostic.

Note: the OCR model *interprets* rather than reads glyphs verbatim — it
may normalise spelling/diacritics. Beneficial for noisy scans; verified
against a true-glyph fixture in the gated live test.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from pathlib import Path

_OCR_ROUTE = "/v1/ocr"


class OcrError(RuntimeError):
    """Raised when OCR cannot produce text for a document."""


def needs_ocr(*, page_count: int, total_chars: int, min_chars_per_page: int) -> bool:
    """True when a paged document yielded too little text to be searchable."""
    if page_count <= 0:
        return False
    return (total_chars / page_count) < min_chars_per_page


async def ocr_pdf_to_markdown(
    source_path: Path,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,  # noqa: ASYNC109 — httpx owns the timeout; no asyncio.timeout wrapper wanted
) -> str:
    """Return the document's text as markdown, one section per page.

    Raises :class:`OcrError` on an unreadable source, transport failure,
    non-2xx response, or an empty OCR result.
    """
    url = f"{base_url.rstrip('/')}{_OCR_ROUTE}"

    try:
        payload = await asyncio.to_thread(source_path.read_bytes)
    except OSError as exc:
        msg = f"Could not read source file for OCR: {exc}"
        raise OcrError(msg) from exc

    document_url = "data:application/pdf;base64," + base64.b64encode(payload).decode()
    body = {
        "model": model,
        "document": {"type": "document_url", "document_url": document_url},
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        msg = f"OCR request failed: {exc}"
        raise OcrError(msg) from exc

    if not response.is_success:
        detail = response.text[:300]
        msg = f"OCR API returned {response.status_code}: {detail}"
        raise OcrError(msg)

    try:
        pages = response.json()["pages"]
        markdown = "\n\n".join(page["markdown"] for page in pages)
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"Unexpected OCR response shape: {exc}"
        raise OcrError(msg) from exc

    if not markdown.strip():
        msg = "OCR produced no text"
        raise OcrError(msg)
    return markdown
