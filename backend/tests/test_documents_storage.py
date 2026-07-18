"""Tests for the streaming upload writer."""

from __future__ import annotations

import hashlib
import io
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import UploadFile

from unstash.documents.storage import (
    UploadTooLargeError,
    safe_storage_leaf,
    write_uploaded_file,
)

if TYPE_CHECKING:
    from pathlib import Path

ORG_ID = uuid.uuid4()
DOCUMENT_ID = uuid.uuid4()


def _upload(data: bytes, filename: str | None = "report.pdf") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


async def test_round_trip_writes_bytes_size_and_hash(tmp_path: Path) -> None:
    payload = b"protocol body " * 1000

    stored = await write_uploaded_file(
        upload=_upload(payload),
        documents_root=tmp_path,
        org_id=ORG_ID,
        document_id=DOCUMENT_ID,
        max_bytes=len(payload),
    )

    expected_path = tmp_path / str(ORG_ID) / str(DOCUMENT_ID) / "report.pdf"
    assert stored.path == expected_path
    assert expected_path.read_bytes() == payload
    assert stored.size_bytes == len(payload)
    assert stored.sha256_hex == hashlib.sha256(payload).hexdigest()


async def test_payload_larger_than_read_chunk_streams_completely(
    tmp_path: Path,
) -> None:
    # Larger than the 64 KiB read chunk so the loop iterates.
    payload = bytes(range(256)) * 1024  # 256 KiB

    stored = await write_uploaded_file(
        upload=_upload(payload),
        documents_root=tmp_path,
        org_id=ORG_ID,
        document_id=DOCUMENT_ID,
        max_bytes=len(payload) + 1,
    )

    assert stored.size_bytes == len(payload)
    assert stored.path.read_bytes() == payload


async def test_payload_at_exactly_max_bytes_is_accepted(tmp_path: Path) -> None:
    payload = b"x" * 1024

    stored = await write_uploaded_file(
        upload=_upload(payload),
        documents_root=tmp_path,
        org_id=ORG_ID,
        document_id=DOCUMENT_ID,
        max_bytes=1024,
    )

    assert stored.size_bytes == 1024


async def test_oversize_upload_raises_and_removes_partial_file(
    tmp_path: Path,
) -> None:
    payload = b"y" * (128 * 1024)

    with pytest.raises(UploadTooLargeError):
        await write_uploaded_file(
            upload=_upload(payload),
            documents_root=tmp_path,
            org_id=ORG_ID,
            document_id=DOCUMENT_ID,
            max_bytes=64 * 1024,
        )

    target = tmp_path / str(ORG_ID) / str(DOCUMENT_ID) / "report.pdf"
    assert not target.exists()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("/etc/cron.d/pwn", "pwn"),
        ("../../../other/evil.pdf", "evil.pdf"),
        ("../../..", f"{DOCUMENT_ID}.bin"),
        ("..", f"{DOCUMENT_ID}.bin"),
        (".", f"{DOCUMENT_ID}.bin"),
        ("", f"{DOCUMENT_ID}.bin"),
        (None, f"{DOCUMENT_ID}.bin"),
        ("weird...name.txt", "weird...name.txt"),
    ],
)
def test_safe_storage_leaf(filename: str | None, expected: str) -> None:
    assert safe_storage_leaf(filename, DOCUMENT_ID) == expected


@pytest.mark.parametrize(
    "filename",
    ["/etc/cron.d/pwn", "../../../victim/x/evil.pdf", "../../escape.txt"],
)
async def test_malicious_filename_stays_inside_document_dir(
    tmp_path: Path,
    filename: str,
) -> None:
    payload = b"hostile"
    doc_dir = tmp_path / str(ORG_ID) / str(DOCUMENT_ID)

    stored = await write_uploaded_file(
        upload=_upload(payload, filename=filename),
        documents_root=tmp_path,
        org_id=ORG_ID,
        document_id=DOCUMENT_ID,
        max_bytes=1024,
    )

    assert stored.path.parent == doc_dir
    assert stored.path.resolve().is_relative_to(doc_dir.resolve())
    assert stored.path.read_bytes() == payload
    # Nothing was written outside the org's document tree.
    assert not (tmp_path / "etc").exists()
    assert not (tmp_path / "victim").exists()
    assert not (tmp_path / "escape.txt").exists()


async def test_missing_filename_falls_back_to_document_id(tmp_path: Path) -> None:
    payload = b"anonymous bytes"

    stored = await write_uploaded_file(
        upload=_upload(payload, filename=None),
        documents_root=tmp_path,
        org_id=ORG_ID,
        document_id=DOCUMENT_ID,
        max_bytes=1024,
    )

    assert stored.path.name == f"{DOCUMENT_ID}.bin"
    assert stored.path.read_bytes() == payload


class _ExplodingUpload:
    """Upload whose stream fails after the first chunk."""

    filename = "flaky.pdf"

    def __init__(self) -> None:
        self._calls = 0

    async def read(self, size: int) -> bytes:
        self._calls += 1
        if self._calls == 1:
            return b"z" * size
        msg = "connection reset"
        raise ConnectionError(msg)


async def test_read_failure_mid_stream_removes_partial_file(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConnectionError):
        await write_uploaded_file(
            upload=cast("UploadFile", _ExplodingUpload()),
            documents_root=tmp_path,
            org_id=ORG_ID,
            document_id=DOCUMENT_ID,
            max_bytes=10 * 1024 * 1024,
        )

    target = tmp_path / str(ORG_ID) / str(DOCUMENT_ID) / "flaky.pdf"
    assert not target.exists()
