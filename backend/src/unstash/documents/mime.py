"""Content-based MIME detection.

The operator-supplied ``Content-Type`` on a multipart upload is not
trustworthy — a renamed ``.exe`` can claim to be ``application/pdf``,
and many legitimate clients (curl with default flags, command-line
HTTP libraries, browsers in certain edge cases) supply
``application/octet-stream`` for everything. We sniff the first few
hundred bytes via libmagic and use that as the authoritative MIME
type for routing decisions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import magic

if TYPE_CHECKING:
    from pathlib import Path


def detect_mime(file_path: Path) -> str:
    """Return libmagic's MIME guess for the file at ``file_path``.

    libmagic reads the first few KB of the file and returns a MIME
    type based on magic bytes (e.g. ``%PDF`` for PDFs, the OOXML zip
    signature plus content-type marker for DOCX). Slightly slower
    than reading the file extension but immune to spoofing.
    """
    # python-magic ships with limited type stubs; this is documented
    # to return a `str` when ``mime=True``.
    return magic.from_file(str(file_path), mime=True)  # pyright: ignore[reportUnknownMemberType]
