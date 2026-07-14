"""Swedish named-entity recognition for universal metadata.

KB-BERT (KBLab) tags people, organisations, locations, and times in
Swedish text. The model is loaded lazily and cached per process — heavy,
so only the worker pays the transformers + torch cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from transformers import Pipeline

_NER_MODEL = "KBLab/bert-base-swedish-cased-ner"

# Below this confidence a tag is treated as noise.
_MIN_SCORE = 0.7

# KB-BERT (SUC 3.0) leading tag -> our category. Compound tags (LOC/ORG)
# map by their first component. OBJ/WRK/MSR (products, works, measures)
# are not indexed here; whether company brands tagged OBJ should count as
# organisations is a real-data tuning point.
_PRIMARY_LABEL = {
    "PER": "person",
    "PRS": "person",
    "ORG": "organisation",
    "LOC": "location",
    "TME": "time",
    "EVN": "event",
}


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    """A named entity found in text, its category, and confidence."""

    text: str
    label: str
    score: float


@lru_cache(maxsize=1)
def _get_pipeline() -> Pipeline:
    """Build (or return cached) the KB-BERT NER pipeline."""
    from transformers import (  # noqa: PLC0415
        AutoModelForTokenClassification,
        AutoTokenizer,
        pipeline,
    )

    # aggregation_strategy glues sub-word tokens into whole-entity spans.
    return pipeline(
        "ner",
        model=AutoModelForTokenClassification.from_pretrained(_NER_MODEL),  # pyright: ignore[reportUnknownArgumentType]
        tokenizer=AutoTokenizer.from_pretrained(_NER_MODEL),  # pyright: ignore[reportUnknownArgumentType]
        aggregation_strategy="first",
    )


def _map_label(entity_group: str) -> str | None:
    return _PRIMARY_LABEL.get(entity_group.split("/", 1)[0])


def extract_entities(text: str) -> list[ExtractedEntity]:
    """Return the named entities KB-BERT finds in Swedish ``text``.

    De-duplicated by (text, label); entities below the confidence floor
    or outside the person/organisation/location/time/event set are dropped.
    """
    if not text.strip():
        return []

    results: list[dict[str, Any]] = _get_pipeline()(text)
    entities: list[ExtractedEntity] = []
    seen: set[tuple[str, str]] = set()
    for item in results:
        label = _map_label(str(item["entity_group"]))
        score = float(item["score"])
        word = str(item["word"]).strip()
        if label is None or score < _MIN_SCORE or not word:
            continue
        key = (word.casefold(), label)
        if key in seen:
            continue
        seen.add(key)
        entities.append(ExtractedEntity(text=word, label=label, score=score))
    return entities
