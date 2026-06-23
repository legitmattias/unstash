"""On-disk storage for uploaded documents.

Files live under ``{documents_root}/{org_id}/{document_id}/{filename}``.
The org subdirectory makes manual inspection easy and gives a natural
boundary for future cleanup (e.g. when an org is deleted, the whole
subtree can be removed). The per-document subdirectory leaves room
for derived artifacts (extracted text, thumbnails) without crowding
the upload path.

The streaming write here computes the SHA-256 hash in flight, so the
caller does not need a second pass over the bytes. The hash doubles
as the deduplication key (Phase E will check it against existing
documents in the same org).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiofiles

if TYPE_CHECKING:
    import uuid
    from pathlib import Path

    from fastapi import UploadFile


_READ_CHUNK_SIZE = 64 * 1024  # 64 KiB streaming chunks


class UploadTooLargeError(Exception):
    """Raised when an upload exceeds the configured maximum byte size."""


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Result of writing an upload to disk."""

    path: Path
    size_bytes: int
    sha256_hex: str


async def write_uploaded_file(
    upload: UploadFile,
    documents_root: Path,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    max_bytes: int,
) -> StoredFile:
    """Stream the upload to disk and return its size + SHA-256.

    Creates the org/document subdirectories if missing. Enforces
    ``max_bytes`` while streaming — raises :class:`UploadTooLargeError`
    as soon as the limit is exceeded, deleting the partial file to
    avoid disk leakage. The original filename is preserved verbatim
    as the leaf name; the caller is responsible for sanitisation if
    needed.
    """
    # FastAPI's UploadFile.filename is Optional but in practice always
    # set on multipart form fields; defend against the edge case by
    # giving the file a deterministic name.
    filename = upload.filename if upload.filename is not None else f"{document_id}.bin"

    target_dir = documents_root / str(org_id) / str(document_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    hasher = hashlib.sha256()
    written = 0
    try:
        async with aiofiles.open(target_path, "wb") as out:
            while True:
                chunk = await upload.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    msg = f"Upload exceeded {max_bytes} bytes."
                    raise UploadTooLargeError(msg)
                hasher.update(chunk)
                await out.write(chunk)
    except BaseException:
        # Any failure during streaming leaves a partial file on disk;
        # remove it so a retried upload doesn't accumulate orphans.
        target_path.unlink(missing_ok=True)
        raise

    return StoredFile(
        path=target_path,
        size_bytes=written,
        sha256_hex=hasher.hexdigest(),
    )
