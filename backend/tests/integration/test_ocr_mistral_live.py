"""Live Mistral OCR test — opt-in, hits the real API.

Skipped unless ``MISTRAL_API_KEY`` is set (and a TTF font is available to
render the fixture). Uses true Swedish diacritics in the rendered image —
the OCR model normalises text rather than reading glyphs verbatim, so
this guards that real åäö content survives a round trip.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from unstash.documents.ocr import ocr_pdf_to_markdown

_API_KEY = os.environ.get("MISTRAL_API_KEY")
_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

pytestmark = [
    pytest.mark.skipif(
        not _API_KEY,
        reason="MISTRAL_API_KEY not set; live OCR test is opt-in",
    ),
    pytest.mark.skipif(
        not _FONT.exists(),
        reason="no TTF font available to render the fixture",
    ),
]


async def test_mistral_ocr_reads_swedish_scan(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415
    from reportlab.lib.pagesizes import A4  # noqa: PLC0415
    from reportlab.pdfgen import canvas  # noqa: PLC0415

    lines = [
        "PROTOKOLL — Styrelsemöte",
        "Årsavgiften höjs med 3,5 % från 1 juli.",
        "Taket på fastigheten ska renoveras för 1 234 500 kr.",
    ]
    img = Image.new("RGB", (1654, 800), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(_FONT), 42)
    for i, line in enumerate(lines):
        draw.text((150, 150 + i * 90), line, fill="black", font=font)
    png = tmp_path / "scan.png"
    img.save(png)
    pdf = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(pdf), pagesize=A4)
    c.drawImage(str(png), 0, 300, width=A4[0], height=350)
    c.save()

    markdown = await ocr_pdf_to_markdown(
        pdf,
        api_key=_API_KEY or "",
        base_url="https://api.mistral.ai",
        model="mistral-ocr-latest",
        timeout=120.0,
    )

    lowered = markdown.lower()
    assert "årsavgiften" in lowered
    assert "taket" in lowered
    assert "1 234 500" in markdown or "1234500" in markdown.replace(" ", "")
