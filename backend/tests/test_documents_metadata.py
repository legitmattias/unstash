"""Tests for the regex date and amount extractors."""

from __future__ import annotations

import pytest

from unstash.documents.metadata import extract_amounts, extract_dates


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("Beslut fattat 2024-03-15 av styrelsen.", "2024-03-15"),
        ("Mötet hölls 15/3 2024 i lokalen.", "2024-03-15"),
        ("Mötet hölls 15/3/2024.", "2024-03-15"),
        ("Protokoll från 15 mars 2024.", "2024-03-15"),
        ("Stämman i Mars 2024 var välbesökt.", "2024-03"),
    ],
)
def test_extract_dates_formats(text: str, iso: str) -> None:
    dates = extract_dates(text)
    assert [d.iso for d in dates] == [iso]


def test_day_month_year_not_double_counted_as_month_year() -> None:
    # "15 mars 2024" must not also yield "2024-03" from its "mars 2024" tail.
    assert [d.iso for d in extract_dates("Daterat 15 mars 2024.")] == ["2024-03-15"]


def test_extract_dates_dedupes_by_iso() -> None:
    dates = extract_dates("Både 2023-06-05 och 5/6 2023 avser samma dag.")
    assert [d.iso for d in dates] == ["2023-06-05"]


def test_extract_dates_rejects_impossible_values() -> None:
    assert extract_dates("Koden 2024-13-40 är inget datum.") == []


def test_extract_dates_none_present() -> None:
    assert extract_dates("Ingen tidsangivelse alls här.") == []


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("Avgiften är 1 234 567 kr per år.", 1234567.0, "SEK"),
        ("Saldo 1.234.567,89 SEK.", 1234567.89, "SEK"),
        ("Kostnad 1234kr.", 1234.0, "SEK"),
        ("Totalt 500 kronor.", 500.0, "SEK"),
        ("Pris €1 000.", 1000.0, "EUR"),
        (" Range 50 euro.", 50.0, "EUR"),
        ("Höjning 3,5 %.", 3.5, "percent"),
    ],
)
def test_extract_amounts(text: str, value: float, unit: str) -> None:
    amounts = extract_amounts(text)
    assert len(amounts) == 1
    assert amounts[0].value == value
    assert amounts[0].unit == unit


def test_extract_amounts_multiple() -> None:
    amounts = extract_amounts("Intäkt 10 000 kr, kostnad 4 500 kr, marginal 55 %.")
    assert [(a.value, a.unit) for a in amounts] == [
        (10000.0, "SEK"),
        (4500.0, "SEK"),
        (55.0, "percent"),
    ]


def test_extract_amounts_none_present() -> None:
    assert extract_amounts("Ren text utan belopp.") == []
