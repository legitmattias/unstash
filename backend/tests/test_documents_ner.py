"""Tests for the NER extractors (mapping/filtering, model mocked)."""

from __future__ import annotations

from typing import Any

import pytest

from unstash.documents import ner as ner_module
from unstash.documents.ner import (
    ExtractedEntity,
    FakeEntityExtractor,
    KbBertExtractor,
    NullEntityExtractor,
)

_MODEL = "test-ner-model"


def _extractor(min_score: float = 0.7) -> KbBertExtractor:
    return KbBertExtractor(model=_MODEL, min_score=min_score)


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    output: list[dict[str, Any]],
) -> None:
    def _pipeline(_text: str) -> list[dict[str, Any]]:
        return output

    monkeypatch.setattr(ner_module, "_build_pipeline", lambda _model: _pipeline)


def test_maps_labels_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(
        monkeypatch,
        [
            {"entity_group": "PER", "word": "Anna Svensson", "score": 0.99},
            {"entity_group": "LOC", "word": "Stockholm", "score": 0.98},
            {"entity_group": "ORG", "word": "Brf Almen", "score": 0.95},
            {"entity_group": "OBJ", "word": "Volvo", "score": 0.99},  # dropped
            {"entity_group": "PER", "word": "Osäker", "score": 0.30},  # below floor
        ],
    )

    entities = _extractor().extract("...")

    assert entities == [
        ExtractedEntity(text="Anna Svensson", label="person", score=0.99),
        ExtractedEntity(text="Stockholm", label="location", score=0.98),
        ExtractedEntity(text="Brf Almen", label="organisation", score=0.95),
    ]


def test_compound_label_maps_by_first_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(
        monkeypatch,
        [
            {"entity_group": "LOC/ORG", "word": "Kommunen", "score": 0.9},
            {"entity_group": "ORG/PRS", "word": "Handelsbanken", "score": 0.9},
        ],
    )
    entities = _extractor().extract("...")
    assert [(e.text, e.label) for e in entities] == [
        ("Kommunen", "location"),
        ("Handelsbanken", "organisation"),
    ]


def test_deduplicates_by_text_and_label(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(
        monkeypatch,
        [
            {"entity_group": "PER", "word": "Anna", "score": 0.99},
            {"entity_group": "PER", "word": "anna", "score": 0.88},
        ],
    )
    entities = _extractor().extract("...")
    assert len(entities) == 1
    assert entities[0].text == "Anna"


def test_min_score_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(
        monkeypatch,
        [{"entity_group": "PER", "word": "Osäker", "score": 0.50}],
    )
    assert _extractor(min_score=0.9).extract("...") == []
    assert _extractor(min_score=0.4).extract("...") == [
        ExtractedEntity(text="Osäker", label="person", score=0.50),
    ]


def test_empty_text_skips_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(_model: str) -> object:
        pytest.fail("pipeline should not load for empty text")

    monkeypatch.setattr(ner_module, "_build_pipeline", _fail)
    assert _extractor().extract("   ") == []


def test_null_extractor_returns_nothing() -> None:
    assert NullEntityExtractor().extract("Anna Svensson bor i Stockholm.") == []


def test_fake_extractor_is_deterministic() -> None:
    fake = FakeEntityExtractor()
    first = fake.extract("Anna Svensson bor i Stockholm och möter Erik.")
    second = fake.extract("Anna Svensson bor i Stockholm och möter Erik.")
    assert first == second
    words = {e.text for e in first}
    assert {"Svensson", "Stockholm", "Erik"} <= words
    # Sentence-initial "Anna" is capitalised too; the point is determinism
    # and that mid-sentence capitalised words are tagged.
    assert all(e.label == "person" for e in first)
