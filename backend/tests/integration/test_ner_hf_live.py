"""Live NER test — opt-in, hits the hosted HuggingFace endpoint.

Skipped unless both ``NER_API_KEY`` and ``NER_ENDPOINT_URL`` are set. The
endpoint scales to zero, so the first call may cold-start; the extractor
retries through the 503 window, and the generous timeout here covers it.
Guards that a Swedish sentence yields recognisable person/organisation
entities through the real model, not just the fake.
"""

from __future__ import annotations

import os

import pytest

from unstash.inference.hf_ner import HfEndpointExtractor

_API_KEY = os.environ.get("NER_API_KEY")
_ENDPOINT_URL = os.environ.get("NER_ENDPOINT_URL")

pytestmark = pytest.mark.skipif(
    not (_API_KEY and _ENDPOINT_URL),
    reason="NER_API_KEY / NER_ENDPOINT_URL not set; live NER test is opt-in",
)


async def test_hf_endpoint_extracts_swedish_entities() -> None:
    extractor = HfEndpointExtractor(
        endpoint_url=_ENDPOINT_URL or "",
        api_key=_API_KEY or "",
        min_score=0.5,
        # Wide enough to absorb a scale-to-zero cold start.
        timeout=120.0,
    )

    entities = await extractor.extract(
        "Anna Svensson valdes till ordförande för Bostadsrättsföreningen Eken.",
    )

    assert entities, "expected at least one entity from the live model"
    labels = {entity.label for entity in entities}
    # The extractor maps raw token tags (PER/ORG/…) to our categories; the
    # sentence carries a person and an organisation, so at least one of
    # those mapped labels should survive the score threshold.
    assert labels & {"person", "organisation"}
    assert all(entity.score >= 0.5 for entity in entities)
