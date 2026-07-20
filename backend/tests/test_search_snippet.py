"""Unit tests for the search snippet builder."""

from __future__ import annotations

from unstash.search.service import make_snippet


def test_short_text_returned_unchanged() -> None:
    text = "Styrelsen beslutade om taket."
    assert make_snippet(text, "taket") == text


def test_long_text_is_centred_on_the_match() -> None:
    text = "A" * 400 + " avgiften höjs " + "B" * 400
    snippet = make_snippet(text, "avgiften", max_chars=100)
    assert "avgiften" in snippet
    assert len(snippet) <= 100 + 2  # window + ellipses
    assert snippet.startswith("…")
    assert snippet.endswith("…")


def test_matches_swedish_inflection_via_substring() -> None:
    # Query term "avgift" lands inside "avgiften".
    text = "X" * 300 + " föreningens avgiften för året " + "Y" * 300
    snippet = make_snippet(text, "avgift", max_chars=80)
    assert "avgiften" in snippet


def test_no_match_falls_back_to_the_start() -> None:
    text = "Z" * 500
    snippet = make_snippet(text, "ingenting", max_chars=100)
    assert snippet.startswith("Z")
    assert snippet.endswith("…")
    assert len(snippet) <= 101


def test_short_query_terms_are_ignored_for_positioning() -> None:
    # "om" is too short to anchor; the real term "taket" positions the window.
    text = "A" * 300 + " om taket " + "B" * 300
    snippet = make_snippet(text, "om taket", max_chars=80)
    assert "taket" in snippet
