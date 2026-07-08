"""Tests for the parse-strategy router.

MIME types are spelled out as literals rather than imported from the
module's routing tables, so an accidental edit to a table breaks a
test instead of silently retargeting it.
"""

from __future__ import annotations

import pytest

from unstash.documents.strategy import ParseStrategy, select_strategy

EXTRACT_MIMES = [
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/html",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]

CONVERT_MIMES = [
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/rtf",
    "text/rtf",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
]

METADATA_ONLY_MIMES = [
    "application/zip",
    "application/x-tar",
    "application/gzip",
    "application/x-7z-compressed",
    # Covered by the image/ prefix rule, not the explicit set.
    "image/png",
    "image/jpeg",
    "image/tiff",
]

SKIP_MIMES = [
    "application/octet-stream",
    "application/x-dosexec",
    "application/javascript",
    "video/mp4",
    "audio/mpeg",
    "message/rfc822",
    "",
]


@pytest.mark.parametrize("mime", EXTRACT_MIMES)
def test_extract_mimes_route_to_extract(mime: str) -> None:
    assert select_strategy(mime) is ParseStrategy.EXTRACT


@pytest.mark.parametrize("mime", CONVERT_MIMES)
def test_legacy_office_mimes_route_to_convert(mime: str) -> None:
    assert select_strategy(mime) is ParseStrategy.CONVERT_THEN_EXTRACT


@pytest.mark.parametrize("mime", METADATA_ONLY_MIMES)
def test_images_and_archives_route_to_metadata_only(mime: str) -> None:
    assert select_strategy(mime) is ParseStrategy.METADATA_ONLY


@pytest.mark.parametrize("mime", SKIP_MIMES)
def test_unknown_mimes_route_to_skip(mime: str) -> None:
    assert select_strategy(mime) is ParseStrategy.SKIP


def test_no_mime_routes_to_more_than_one_strategy() -> None:
    """The four literal lists above must not overlap."""
    all_mimes = EXTRACT_MIMES + CONVERT_MIMES + METADATA_ONLY_MIMES + SKIP_MIMES
    assert len(all_mimes) == len(set(all_mimes))
