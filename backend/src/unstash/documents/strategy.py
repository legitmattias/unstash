"""Strategy router for the parse pipeline.

Maps a detected MIME type to one of four strategies, which the
ingest task uses to dispatch the right handler:

- ``EXTRACT`` — Docling can read it directly. PDF, plain text,
  markdown. The default happy path.
- ``CONVERT_THEN_EXTRACT`` — needs a format conversion before
  Docling. Office formats (DOCX, ODT, RTF, PPT, etc.). The Gotenberg
  sidecar handles these; introduced in M3-B PR 2.
- ``METADATA_ONLY`` — recognised but not chunkable in M3. Images,
  archives. The document row is created; no chunks are generated.
  A later phase can revisit (image OCR, archive recursion).
- ``SKIP`` — unsupported or actively suspicious. The upload is
  recorded as failed with an explanation; no parsing attempted.

The mapping is intentionally explicit (no "default to EXTRACT") so a
novel MIME type doesn't silently flow into the parser and produce
inscrutable Docling errors.
"""

from __future__ import annotations

import enum


class ParseStrategy(enum.StrEnum):
    """Routing decision for a single document based on its MIME type."""

    EXTRACT = "extract"
    CONVERT_THEN_EXTRACT = "convert_then_extract"
    METADATA_ONLY = "metadata_only"
    SKIP = "skip"


# Direct-extraction MIME types — Docling handles these natively.
_EXTRACT_MIMES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/html",
        # Excel formats — Docling supports xlsx structure-aware extraction.
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        # PowerPoint and Word OOXML — Docling supports these too.
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)

# Legacy office formats that need Gotenberg conversion before parsing.
# Wired in M3-B PR 2; included here so the routing table is exhaustive.
_CONVERT_MIMES = frozenset(
    {
        "application/msword",  # .doc
        "application/vnd.ms-excel",  # .xls
        "application/vnd.ms-powerpoint",  # .ppt
        "application/rtf",
        "text/rtf",
        "application/vnd.oasis.opendocument.text",  # .odt
        "application/vnd.oasis.opendocument.spreadsheet",  # .ods
        "application/vnd.oasis.opendocument.presentation",  # .odp
    }
)

# File types we'll record but won't chunk in M3.
_METADATA_ONLY_PREFIXES = ("image/",)
_METADATA_ONLY_MIMES = frozenset(
    {
        "application/zip",
        "application/x-tar",
        "application/gzip",
        "application/x-7z-compressed",
    }
)


def select_strategy(mime: str) -> ParseStrategy:
    """Return the parse strategy for the given MIME type.

    Anything not in the explicit allow-lists falls through to
    :class:`ParseStrategy.SKIP` — better to refuse to parse and
    surface the unknown type to the operator than to feed something
    Docling has no chance with into the worker.
    """
    if mime in _EXTRACT_MIMES:
        return ParseStrategy.EXTRACT
    if mime in _CONVERT_MIMES:
        return ParseStrategy.CONVERT_THEN_EXTRACT
    if mime in _METADATA_ONLY_MIMES or any(
        mime.startswith(prefix) for prefix in _METADATA_ONLY_PREFIXES
    ):
        return ParseStrategy.METADATA_ONLY
    return ParseStrategy.SKIP
