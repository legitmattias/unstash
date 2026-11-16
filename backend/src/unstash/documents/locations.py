"""Source locations of documents: path normalisation and recording.

A source path is supplied by the uploading client and describes where the
file sat in the user's own folder structure. It is stored as metadata and
never used to build a filesystem path on the server.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert

from unstash.db.models import DocumentLocation

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

MAX_SOURCE_PATH_LENGTH = 1024


class InvalidSourcePathError(ValueError):
    """Raised when a client-supplied source path cannot be normalised."""


def normalise_source_path(raw: str) -> str:
    """Return ``raw`` as a relative, forward-slash path in NFC form.

    Backslashes are treated as separators, so paths from Windows clients
    compare equal to the same path from other clients. Empty and ``.``
    segments are dropped, which also removes leading and trailing slashes.
    Unicode is composed to NFC because macOS clients send decomposed
    names, which would otherwise differ byte-for-byte from the same name
    sent by another client.

    Raises :class:`InvalidSourcePathError` for a path that is empty after
    normalisation, exceeds :data:`MAX_SOURCE_PATH_LENGTH`, contains a
    ``..`` segment, or contains control characters.
    """
    composed = unicodedata.normalize("NFC", raw).replace("\\", "/")
    if any(unicodedata.category(char) == "Cc" for char in composed):
        msg = "Source path contains control characters."
        raise InvalidSourcePathError(msg)

    segments = [segment for segment in composed.split("/") if segment not in {"", "."}]
    if ".." in segments:
        msg = "Source path must not contain parent references."
        raise InvalidSourcePathError(msg)
    if not segments:
        msg = "Source path is empty."
        raise InvalidSourcePathError(msg)

    path = "/".join(segments)
    if len(path) > MAX_SOURCE_PATH_LENGTH:
        msg = f"Source path exceeds {MAX_SOURCE_PATH_LENGTH} characters."
        raise InvalidSourcePathError(msg)
    return path


async def record_location(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    path: str,
    source_modified_at: datetime | None,
) -> None:
    """Record a manual-upload location for a document.

    ``path`` must already be normalised. Recording a path the document
    already has is a no-op, so repeating an import does not duplicate
    locations.
    """
    await session.execute(
        insert(DocumentLocation)
        .values(
            org_id=org_id,
            document_id=document_id,
            path=path,
            source_modified_at=source_modified_at,
        )
        .on_conflict_do_nothing(),
    )
