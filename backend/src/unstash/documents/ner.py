"""Named-entity recognition behind a config-switched backend.

Defines the ``EntityExtractor`` protocol, its data type, a local KB-BERT
implementation, a null backend (NER disabled), a deterministic fake, and
the factory that selects the configured backend. Per ADR 0009, model
choice and thresholds are configuration, not code — and the extractor is
swappable (local model vs a future hosted endpoint) without touching
callers.

The KB-BERT model is loaded lazily and cached per process — heavy, so
only the worker pays the transformers + torch cost.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from unstash.config import Settings

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


class EntityExtractor(Protocol):
    """Finds named entities in text. Implemented per backend and by a fake.

    ``extract`` is async so a hosted-endpoint backend can make an HTTP call
    on the same seam; local backends wrap their blocking work in a thread.
    """

    async def extract(self, text: str) -> list[ExtractedEntity]:
        """Return the named entities found in ``text``."""
        ...


class _NerPipeline(Protocol):
    """The slice of transformers' NER pipeline surface we call."""

    def __call__(self, text: str) -> list[dict[str, Any]]: ...


@lru_cache(maxsize=2)
def _build_pipeline(model: str) -> _NerPipeline:
    """Build (or return cached) a transformers NER pipeline for ``model``."""
    from transformers import pipeline  # noqa: PLC0415

    # transformers publishes no usable types for pipeline(); the runtime
    # call is covered by the real-model integration test.
    # aggregation_strategy glues sub-word tokens into whole-entity spans.
    return cast(
        "_NerPipeline",
        pipeline(  # pyright: ignore[reportCallIssue, reportUnknownArgumentType]
            "ner",  # pyright: ignore[reportArgumentType]
            model=model,
            tokenizer=model,
            aggregation_strategy="first",
        ),
    )


def _map_label(entity_group: str) -> str | None:
    return _PRIMARY_LABEL.get(entity_group.split("/", 1)[0])


def entities_from_tagged(
    results: list[dict[str, Any]],
    min_score: float,
) -> list[ExtractedEntity]:
    """Map aggregated token-classification spans to our entity categories.

    Shared by every token-classification backend (local pipeline or hosted
    endpoint), which return the same ``entity_group``/``score``/``word``
    shape. De-duplicated by (text, label); spans below the confidence floor
    or outside the mapped category set are dropped.
    """
    entities: list[ExtractedEntity] = []
    seen: set[tuple[str, str]] = set()
    for item in results:
        label = _map_label(str(item["entity_group"]))
        score = float(item["score"])
        word = str(item["word"]).strip()
        if label is None or score < min_score or not word:
            continue
        key = (word.casefold(), label)
        if key in seen:
            continue
        seen.add(key)
        entities.append(ExtractedEntity(text=word, label=label, score=score))
    return entities


class KbBertExtractor:
    """Local KB-BERT extractor (transformers + torch, worker-only)."""

    def __init__(self, *, model: str, min_score: float) -> None:
        """Configure the model and confidence floor; no model loads yet."""
        self._model = model
        self._min_score = min_score

    async def extract(self, text: str) -> list[ExtractedEntity]:
        """Return the named entities KB-BERT finds in Swedish ``text``.

        The transformers pipeline is blocking, so it runs in a thread to
        keep the worker's event loop responsive. De-duplicated by (text,
        label); entities below the confidence floor or outside the
        person/organisation/location/time/event set are dropped.
        """
        if not text.strip():
            return []
        return await asyncio.to_thread(self._extract_sync, text)

    def _extract_sync(self, text: str) -> list[ExtractedEntity]:
        results: list[dict[str, Any]] = _build_pipeline(self._model)(text)
        return entities_from_tagged(results, self._min_score)


class NullEntityExtractor:
    """No-op extractor for when NER is disabled."""

    async def extract(self, text: str) -> list[ExtractedEntity]:
        """Return no entities."""
        _ = text
        return []


class FakeEntityExtractor:
    """Deterministic offline extractor for tests.

    Tags each capitalised, non-sentence-initial word as a person, so
    ingest wiring can be exercised without loading a model.
    """

    _MIN_TOKEN_LEN = 2

    async def extract(self, text: str) -> list[ExtractedEntity]:
        """Return capitalised mid-sentence words as person entities."""
        entities: list[ExtractedEntity] = []
        seen: set[str] = set()
        for word in text.split():
            token = word.strip(".,;:!?()\"'")
            if len(token) < self._MIN_TOKEN_LEN or not token[0].isupper() or token.isupper():
                continue
            key = token.casefold()
            if key in seen:
                continue
            seen.add(key)
            entities.append(ExtractedEntity(text=token, label="person", score=1.0))
        return entities


def get_entity_extractor(settings: Settings) -> EntityExtractor:
    """Return the configured entity extractor for ``settings.ner_backend``."""
    if settings.ner_backend == "kb-bert":
        return KbBertExtractor(model=settings.ner_model, min_score=settings.ner_min_score)
    if settings.ner_backend == "hf":
        # Imported here so the transformers-free hosted path doesn't pull in
        # the provider module unless selected.
        from unstash.inference.hf_ner import HfEndpointExtractor  # noqa: PLC0415

        return HfEndpointExtractor(
            endpoint_url=settings.ner_endpoint_url,
            api_key=settings.ner_api_key,
            min_score=settings.ner_min_score,
            timeout=settings.ner_timeout_seconds,
        )
    if settings.ner_backend == "fake":
        return FakeEntityExtractor()
    return NullEntityExtractor()
