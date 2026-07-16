"""Tests for the KB-BERT NER wrapper (mapping/filtering, model mocked)."""

from __future__ import annotations

from typing import Any

import pytest

from unstash.documents import ner as ner_module
from unstash.documents.ner import ExtractedEntity, extract_entities


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    output: list[dict[str, Any]],
) -> None:
    def _pipeline(_text: str) -> list[dict[str, Any]]:
        return output

    monkeypatch.setattr(ner_module, "_get_pipeline", lambda: _pipeline)


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

    entities = extract_entities("...")

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
    entities = extract_entities("...")
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
    entities = extract_entities("...")
    assert len(entities) == 1
    assert entities[0].text == "Anna"


def test_empty_text_skips_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail() -> object:
        pytest.fail("pipeline should not load for empty text")

    monkeypatch.setattr(ner_module, "_get_pipeline", _fail)
    assert extract_entities("   ") == []
