"""Tests for the folder import command."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from unstash.importer.__main__ import main
from unstash.importer.client import Outcome, upload_item
from unstash.importer.plan import ImportItem, is_ignored, iter_import_items

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, data: bytes = b"content") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_walk_yields_relative_paths_in_sorted_order(tmp_path: Path) -> None:
    _write(tmp_path / "Board" / "2021" / "minutes.pdf")
    _write(tmp_path / "Board" / "2020" / "minutes.pdf")
    _write(tmp_path / "annual.docx")

    items = list(iter_import_items(tmp_path))

    assert [item.source_path for item in items] == [
        "Board/2020/minutes.pdf",
        "Board/2021/minutes.pdf",
        "annual.docx",
    ]
    assert items[0].mime_type == "application/pdf"
    assert items[0].size_bytes == len(b"content")


def test_walk_records_modification_time_in_utc(tmp_path: Path) -> None:
    file = _write(tmp_path / "old.txt")
    stamp = datetime(2009, 3, 14, 12, 0, tzinfo=UTC)
    os.utime(file, (stamp.timestamp(), stamp.timestamp()))

    (item,) = iter_import_items(tmp_path)

    assert item.modified_at == stamp


def test_walk_applies_prefix(tmp_path: Path) -> None:
    _write(tmp_path / "a.txt")

    (item,) = iter_import_items(tmp_path, prefix="/Archive/Shared/")

    assert item.source_path == "Archive/Shared/a.txt"


def test_walk_skips_junk_empty_hidden_and_symlinked_entries(tmp_path: Path) -> None:
    outside = _write(tmp_path / "outside" / "secret.txt")
    root = tmp_path / "root"
    _write(root / "kept.txt")
    _write(root / ".DS_Store")
    _write(root / "Thumbs.db")
    _write(root / "~$draft.docx")
    _write(root / "._kept.txt")
    _write(root / ".git" / "config")
    _write(root / "empty.txt", b"")
    (root / "link.txt").symlink_to(outside)
    (root / "linked-dir").symlink_to(outside.parent, target_is_directory=True)

    assert [item.source_path for item in iter_import_items(root)] == ["kept.txt"]


def test_unknown_extension_falls_back_to_octet_stream(tmp_path: Path) -> None:
    _write(tmp_path / "data.unknownext")

    (item,) = iter_import_items(tmp_path)

    assert item.mime_type == "application/octet-stream"


@pytest.mark.parametrize("name", [".DS_Store", "thumbs.db", "~$report.docx", ".~lock.a.odt#"])
def test_is_ignored(name: str) -> None:
    assert is_ignored(name)


def _item(tmp_path: Path) -> ImportItem:
    file = _write(tmp_path / "Board" / "minutes.txt", b"minutes")
    return ImportItem(
        file=file,
        source_path="Board/minutes.txt",
        modified_at=datetime(2021, 5, 4, 8, 15, tzinfo=UTC),
        size_bytes=7,
        mime_type="text/plain",
    )


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(base_url="http://service.test", transport=handler)


def test_upload_sends_file_path_and_modification_time(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"document_id": "x", "job_id": "y"})

    with _client(httpx.MockTransport(handler)) as client:
        outcome = upload_item(client, "acme", _item(tmp_path))

    assert outcome is Outcome.CREATED
    (request,) = seen
    assert request.url.path == "/api/orgs/acme/documents"
    body = request.read()
    assert b'name="source_path"\r\n\r\nBoard/minutes.txt' in body
    assert b'name="source_modified_at"\r\n\r\n2021-05-04T08:15:00+00:00' in body
    assert b'filename="minutes.txt"' in body
    assert b"minutes" in body


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, Outcome.DUPLICATE),
        (413, Outcome.TOO_LARGE),
        (422, Outcome.REJECTED),
        (403, Outcome.REJECTED),
    ],
)
def test_final_responses_are_not_retried(tmp_path: Path, status: int, expected: Outcome) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={})

    with _client(httpx.MockTransport(handler)) as client:
        outcome = upload_item(client, "acme", _item(tmp_path), sleep=lambda _: None)

    assert outcome is expected
    assert calls == 1


def test_rate_limit_is_retried_after_the_advertised_delay(tmp_path: Path) -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(503),
        httpx.Response(201, json={}),
    ]
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    with _client(httpx.MockTransport(handler)) as client:
        outcome = upload_item(client, "acme", _item(tmp_path), sleep=delays.append)

    assert outcome is Outcome.CREATED
    assert delays == [7.0, 4.0]


def test_persistent_transport_errors_give_up(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused", request=request)

    delays: list[float] = []
    with _client(httpx.MockTransport(handler)) as client:
        outcome = upload_item(client, "acme", _item(tmp_path), sleep=delays.append)

    assert outcome is Outcome.FAILED
    assert calls == 5
    assert len(delays) == 4


def test_dry_run_lists_files_without_reading_the_token(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "root"
    _write(root / "Board" / "minutes.txt", b"minutes")

    code = main(
        [
            str(root),
            "--url",
            "http://service.test",
            "--org",
            "acme",
            "--token-file",
            str(tmp_path / "missing-token"),
            "--dry-run",
        ],
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Board/minutes.txt" in out
    assert "1 files, 7 bytes" in out


def test_missing_root_is_a_usage_error(tmp_path: Path) -> None:
    code = main(
        [
            str(tmp_path / "absent"),
            "--url",
            "http://service.test",
            "--org",
            "acme",
            "--token-file",
            str(tmp_path / "token"),
        ],
    )

    assert code == 2
