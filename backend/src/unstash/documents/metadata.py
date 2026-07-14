"""Universal metadata extraction — dates and amounts (regex).

Best-effort, model-free structured signals pulled from document text for
search filters and faceting. Entity extraction (people/orgs/locations) is
handled separately by the model-backed NER extractor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SWEDISH_MONTHS = {
    "januari": 1,
    "februari": 2,
    "mars": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "augusti": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}
_MONTH_ALT = "|".join(_SWEDISH_MONTHS)

# 2024-03-15
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# 15/3 2024 or 15/3/2024
_DMY_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})[ /-](\d{4})\b")
# 15 mars 2024
_DMY_TEXT = re.compile(rf"\b(\d{{1,2}}) ({_MONTH_ALT}) (\d{{4}})\b", re.IGNORECASE)
# mars 2024 (no day)
_MY_TEXT = re.compile(rf"\b({_MONTH_ALT}) (\d{{4}})\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ExtractedDate:
    """A date found in text, plus its normalised ISO-8601 form.

    ``iso`` is ``YYYY-MM-DD`` when the day is known, otherwise ``YYYY-MM``.
    """

    raw: str
    iso: str


@dataclass(frozen=True, slots=True)
class ExtractedAmount:
    """A monetary amount or percentage found in text.

    ``value`` is the parsed number (``None`` if it could not be parsed);
    ``unit`` is ``SEK``, ``EUR``, or ``percent``.
    """

    raw: str
    value: float | None
    unit: str


def _valid_month_day(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= 31  # noqa: PLR2004


def extract_dates(text: str) -> list[ExtractedDate]:
    """Return the dates found in ``text``, de-duplicated by ISO form.

    Handles ISO (``2024-03-15``), numeric Swedish (``15/3 2024``),
    day-month-year (``15 mars 2024``), and month-year (``mars 2024``).
    Overlapping sub-matches (the ``mars 2024`` inside ``15 mars 2024``)
    are not double-counted.
    """
    found: list[ExtractedDate] = []
    seen_iso: set[str] = set()
    claimed: list[tuple[int, int]] = []

    def _add(match: re.Match[str], iso: str) -> None:
        span = match.span()
        if any(start <= span[0] and span[1] <= end for start, end in claimed):
            return
        claimed.append(span)
        if iso not in seen_iso:
            seen_iso.add(iso)
            found.append(ExtractedDate(raw=match.group(0), iso=iso))

    for match in _ISO_DATE.finditer(text):
        year, month, day = (int(match.group(i)) for i in (1, 2, 3))
        if _valid_month_day(month, day):
            _add(match, f"{year:04d}-{month:02d}-{day:02d}")

    for match in _DMY_SLASH.finditer(text):
        day, month, year = (int(match.group(i)) for i in (1, 2, 3))
        if _valid_month_day(month, day):
            _add(match, f"{year:04d}-{month:02d}-{day:02d}")

    for match in _DMY_TEXT.finditer(text):
        day = int(match.group(1))
        month = _SWEDISH_MONTHS[match.group(2).lower()]
        year = int(match.group(3))
        if _valid_month_day(month, day):
            _add(match, f"{year:04d}-{month:02d}-{day:02d}")

    for match in _MY_TEXT.finditer(text):
        month = _SWEDISH_MONTHS[match.group(1).lower()]
        year = int(match.group(2))
        _add(match, f"{year:04d}-{month:02d}")

    return found


# A grouped number: 1234, 1 234 567, 1.234.567,89, 1234,50
_NUMBER = r"\d{1,3}(?:[ .]\d{3})*(?:,\d+)?|\d+(?:,\d+)?"
_SEK = re.compile(rf"\b({_NUMBER})\s*(?:kr|kronor|sek)\b", re.IGNORECASE)
_EUR = re.compile(rf"(?:€\s*({_NUMBER})|\b({_NUMBER})\s*(?:eur|euro)\b)", re.IGNORECASE)
_PERCENT = re.compile(rf"({_NUMBER})\s*%")


def _parse_swedish_number(raw: str) -> float | None:
    """Parse a Swedish-formatted number: space/'.' thousands, ',' decimal."""
    cleaned = raw.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_amounts(text: str) -> list[ExtractedAmount]:
    """Return monetary amounts (SEK, EUR) and percentages found in ``text``."""
    found: list[ExtractedAmount] = []

    for match in _SEK.finditer(text):
        found.append(
            ExtractedAmount(
                raw=match.group(0),
                value=_parse_swedish_number(match.group(1)),
                unit="SEK",
            ),
        )
    for match in _EUR.finditer(text):
        number = match.group(1) or match.group(2)
        found.append(
            ExtractedAmount(
                raw=match.group(0),
                value=_parse_swedish_number(number),
                unit="EUR",
            ),
        )
    for match in _PERCENT.finditer(text):
        found.append(
            ExtractedAmount(
                raw=match.group(0),
                value=_parse_swedish_number(match.group(1)),
                unit="percent",
            ),
        )

    return found
