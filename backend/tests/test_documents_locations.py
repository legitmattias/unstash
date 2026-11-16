"""Tests for source-path normalisation."""

from __future__ import annotations

import unicodedata

import pytest

from unstash.documents.locations import (
    MAX_SOURCE_PATH_LENGTH,
    InvalidSourcePathError,
    normalise_source_path,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Board/2021/minutes.pdf", "Board/2021/minutes.pdf"),
        ("/Board/2021/minutes.pdf", "Board/2021/minutes.pdf"),
        ("Board//2021/./minutes.pdf/", "Board/2021/minutes.pdf"),
        ("Board\\2021\\minutes.pdf", "Board/2021/minutes.pdf"),
        ("minutes.pdf", "minutes.pdf"),
        ("Board/annual report 2021.pdf", "Board/annual report 2021.pdf"),
    ],
)
def test_normalises_separators_and_redundant_segments(raw: str, expected: str) -> None:
    assert normalise_source_path(raw) == expected


def test_decomposed_and_composed_names_normalise_to_the_same_path() -> None:
    composed = "Årsmöte/kallelse.pdf"
    decomposed = unicodedata.normalize("NFD", composed)
    assert decomposed != composed

    assert normalise_source_path(decomposed) == normalise_source_path(composed) == composed


@pytest.mark.parametrize(
    "raw",
    [
        "../secrets.txt",
        "Board/../../etc/passwd",
        "Board\\..\\minutes.pdf",
        "",
        "/",
        "./.",
        "Board/min\x00utes.pdf",
        "Board/minutes\n.pdf",
    ],
)
def test_rejects_unsafe_or_empty_paths(raw: str) -> None:
    with pytest.raises(InvalidSourcePathError):
        normalise_source_path(raw)


def test_segment_containing_dots_is_not_a_parent_reference() -> None:
    assert normalise_source_path("Board/..minutes..pdf") == "Board/..minutes..pdf"


def test_length_limit_applies_to_the_normalised_path() -> None:
    at_limit = "a" * MAX_SOURCE_PATH_LENGTH
    assert normalise_source_path(at_limit) == at_limit

    with pytest.raises(InvalidSourcePathError):
        normalise_source_path(at_limit + "a")
