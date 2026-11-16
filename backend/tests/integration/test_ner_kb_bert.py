"""Real KB-BERT NER — validates the live model output against our mapping.

Loads the actual model (cached in CI via the HuggingFace cache), so a
transformers upgrade or a change in the model's label scheme that breaks
aggregation or our mapping surfaces here rather than in production.
"""

from __future__ import annotations

from unstash.documents.ner import KbBertExtractor

_MIN_SCORE = 0.7


async def test_kb_bert_extracts_swedish_person_and_location() -> None:
    extractor = KbBertExtractor(
        model="KBLab/bert-base-swedish-cased-ner",
        min_score=_MIN_SCORE,
    )
    entities = await extractor.extract("Anna Svensson bor i Stockholm.")

    found = {(e.text, e.label) for e in entities}
    assert ("Anna Svensson", "person") in found
    assert ("Stockholm", "location") in found
    assert all(e.score >= _MIN_SCORE for e in entities)


async def test_kb_bert_handles_text_beyond_the_model_window() -> None:
    extractor = KbBertExtractor(
        model="KBLab/bert-base-swedish-cased-ner",
        min_score=_MIN_SCORE,
    )
    filler = "Styrelsen diskuterade ekonomin och underhållet av fastigheten under mötet. " * 120
    entities = await extractor.extract(
        filler + "Protokollet justerades av Erik Andersson i Uppsala."
    )

    found = {(e.text, e.label) for e in entities}
    assert ("Erik Andersson", "person") in found
    assert ("Uppsala", "location") in found
