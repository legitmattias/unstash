"""Tests for the Gotenberg conversion client.

The real Gotenberg sidecar is exercised by manual/staging smoke tests;
these unit tests stub the HTTP layer with ``httpx.MockTransport`` so the
client's own logic — route, multipart field, success decoding, and the
error translations — is verified without a network dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx
import pytest

from unstash.documents import conversion
from unstash.documents.conversion import ConversionError, convert_to_pdf

if TYPE_CHECKING:
    from pathlib import Path

_URL = "http://gotenberg:3000"
_TIMEOUT = 30.0
_MAX_BYTES = 10 * 1024 * 1024

Handler = Callable[[httpx.Request], httpx.Response]


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    """Route the client's AsyncClient through a MockTransport."""
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(conversion.httpx, "AsyncClient", factory)


async def test_posts_file_to_libreoffice_route_and_returns_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "protocol.doc"
    source.write_bytes(b"legacy word bytes")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, content=b"%PDF-1.7 converted")

    _install_transport(monkeypatch, handler)

    result = await convert_to_pdf(
        source, gotenberg_url=_URL, max_bytes=_MAX_BYTES, timeout=_TIMEOUT
    )

    assert result == b"%PDF-1.7 converted"
    assert captured["url"] == f"{_URL}/forms/libreoffice/convert"
    assert captured["method"] == "POST"
    assert "multipart/form-data" in captured["content_type"]  # type: ignore[operator]
    # The multipart body carries the field name and the original filename
    # (Gotenberg picks the LibreOffice filter from the extension).
    body = captured["body"]
    assert isinstance(body, bytes)
    assert b'name="files"' in body
    assert b"protocol.doc" in body


async def test_trailing_slash_in_base_url_is_normalised(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "x.doc"
    source.write_bytes(b"bytes")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"%PDF")

    _install_transport(monkeypatch, handler)

    await convert_to_pdf(source, gotenberg_url=f"{_URL}/", max_bytes=_MAX_BYTES, timeout=_TIMEOUT)

    assert seen["url"] == f"{_URL}/forms/libreoffice/convert"


async def test_non_success_status_raises_conversion_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "x.doc"
    source.write_bytes(b"bytes")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="LibreOffice is unavailable")

    _install_transport(monkeypatch, handler)

    with pytest.raises(ConversionError) as excinfo:
        await convert_to_pdf(source, gotenberg_url=_URL, max_bytes=_MAX_BYTES, timeout=_TIMEOUT)

    assert "503" in str(excinfo.value)
    assert "LibreOffice is unavailable" in str(excinfo.value)


async def test_transport_error_raises_conversion_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "x.doc"
    source.write_bytes(b"bytes")

    def handler(_request: httpx.Request) -> httpx.Response:
        msg = "connection refused"
        raise httpx.ConnectError(msg)

    _install_transport(monkeypatch, handler)

    with pytest.raises(ConversionError):
        await convert_to_pdf(source, gotenberg_url=_URL, max_bytes=_MAX_BYTES, timeout=_TIMEOUT)


async def test_unreadable_source_raises_conversion_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "does-not-exist.doc"

    # No transport install needed — the read fails before any request.
    with pytest.raises(ConversionError):
        await convert_to_pdf(missing, gotenberg_url=_URL, max_bytes=_MAX_BYTES, timeout=_TIMEOUT)


async def test_oversized_source_raises_before_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_request: httpx.Request) -> httpx.Response:
        pytest.fail("no request should be sent for an oversized document")

    _install_transport(monkeypatch, _boom)
    source = tmp_path / "huge.doc"
    source.write_bytes(b"x" * 2048)
    with pytest.raises(ConversionError, match="conversion limit"):
        await convert_to_pdf(source, gotenberg_url=_URL, max_bytes=1024, timeout=_TIMEOUT)
