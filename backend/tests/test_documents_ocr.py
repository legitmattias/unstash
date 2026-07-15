"""Tests for the OCR trigger and the Mistral OCR client (HTTP mocked)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx
import pytest

from unstash.documents import ocr as ocr_module
from unstash.documents.ocr import OcrError, needs_ocr, ocr_pdf_to_markdown

if TYPE_CHECKING:
    from pathlib import Path

Handler = Callable[[httpx.Request], httpx.Response]

_URL = "https://api.mistral.ai"
_MODEL = "mistral-ocr-latest"


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ocr_module.httpx, "AsyncClient", factory)


async def _call(path: Path) -> str:
    return await ocr_pdf_to_markdown(
        path,
        api_key="k",
        base_url=_URL,
        model=_MODEL,
        timeout=10.0,
    )


# --- trigger -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("pages", "chars", "threshold", "expected"),
    [
        (1, 0, 200, True),  # classic scan: zero text
        (10, 500, 200, True),  # 50 chars/page: image-heavy
        (1, 200, 200, False),  # exactly at threshold: keep
        (2, 3000, 200, False),  # normal text document
        (0, 0, 200, False),  # no page semantics (text/markdown)
    ],
)
def test_needs_ocr(pages: int, chars: int, threshold: int, expected: bool) -> None:
    assert needs_ocr(page_count=pages, total_chars=chars, min_chars_per_page=threshold) is expected


# --- client ------------------------------------------------------------------


async def test_sends_base64_data_uri_and_joins_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4 fake scan bytes")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "pages": [
                    {"index": 0, "markdown": "# Sida ett"},
                    {"index": 1, "markdown": "Sida två"},
                ],
                "usage_info": {"pages_processed": 2},
            },
        )

    _install_transport(monkeypatch, handler)

    markdown = await _call(source)

    assert markdown == "# Sida ett\n\nSida två"
    assert captured["url"] == f"{_URL}/v1/ocr"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == _MODEL
    document = body["document"]
    assert isinstance(document, dict)
    assert document["type"] == "document_url"
    assert str(document["document_url"]).startswith("data:application/pdf;base64,")


async def test_non_success_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"bytes")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    _install_transport(monkeypatch, handler)
    with pytest.raises(OcrError) as excinfo:
        await _call(source)
    assert "429" in str(excinfo.value)


async def test_transport_error_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"bytes")

    def handler(_request: httpx.Request) -> httpx.Response:
        msg = "boom"
        raise httpx.ConnectError(msg)

    _install_transport(monkeypatch, handler)
    with pytest.raises(OcrError):
        await _call(source)


async def test_empty_result_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"bytes")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pages": [{"index": 0, "markdown": "  "}]})

    _install_transport(monkeypatch, handler)
    with pytest.raises(OcrError):
        await _call(source)


async def test_unreadable_source_raises(tmp_path: Path) -> None:
    with pytest.raises(OcrError):
        await _call(tmp_path / "missing.pdf")
