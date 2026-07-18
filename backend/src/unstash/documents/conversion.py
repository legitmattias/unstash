"""Legacy office document -> PDF conversion via the Gotenberg sidecar.

Office formats Docling cannot read natively (DOC, XLS, PPT, RTF, ODT,
ODS, ODP) are converted to PDF by Gotenberg's LibreOffice route and then
handed to the normal PDF extraction path. The worker calls this over the
internal Docker network; Gotenberg is never publicly reachable.

Gotenberg selects the LibreOffice import filter from the uploaded file's
extension, so the original filename (with its extension) is sent as-is.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from pathlib import Path

# LibreOffice conversion route. Single file in -> single PDF back;
# multiple files would return a ZIP, but the worker converts one at a time.
_LIBREOFFICE_ROUTE = "/forms/libreoffice/convert"


class ConversionError(RuntimeError):
    """Raised when Gotenberg fails to convert a document to PDF."""


async def convert_to_pdf(
    source_path: Path,
    *,
    gotenberg_url: str,
    max_bytes: int,
    timeout: float,  # noqa: ASYNC109 — httpx owns the timeout; no asyncio.timeout wrapper wanted
) -> bytes:
    """Convert the office document at ``source_path`` to PDF bytes.

    ``max_bytes`` caps the source read so a large document cannot be held
    whole in memory before conversion.

    Posts the file to Gotenberg's LibreOffice route and returns the
    resulting PDF. Raises :class:`ConversionError` on an unreadable or
    oversized source, any transport error, or a non-2xx response —
    carrying Gotenberg's message so the failure is actionable in triage.
    """
    url = f"{gotenberg_url.rstrip('/')}{_LIBREOFFICE_ROUTE}"

    try:
        payload = await asyncio.to_thread(source_path.read_bytes)
    except OSError as exc:
        msg = f"Could not read source file for conversion: {exc}"
        raise ConversionError(msg) from exc

    if len(payload) > max_bytes:
        msg = f"Document is {len(payload)} bytes, over the {max_bytes}-byte conversion limit."
        raise ConversionError(msg)

    files = {"files": (source_path.name, payload)}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, files=files)
    except httpx.HTTPError as exc:
        msg = f"Gotenberg request failed: {exc}"
        raise ConversionError(msg) from exc

    if not response.is_success:
        detail = response.text[:500]
        msg = f"Gotenberg returned {response.status_code}: {detail}"
        raise ConversionError(msg)

    return response.content
