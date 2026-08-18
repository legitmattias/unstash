"""Explain mode: the intermediate rankings the result list hides.

These are unit tests over ``_build_explain`` rather than end-to-end searches.
The assembly is where the useful distinctions live — which leg found a
document, and where it sat before the reranker touched it — and it can be
exercised directly from the rankings the pipeline builds.
"""

from __future__ import annotations

import uuid

from unstash.search.service import SearchHit, _build_explain, _Candidate, _document_ranks


def _row(document_id: uuid.UUID, score: float = 0.0) -> dict[str, object]:
    return {"document_id": document_id, "score": score}


def _candidate(document_id: uuid.UUID, fused: float) -> _Candidate:
    return _Candidate(
        document_id=document_id,
        title="t",
        mime_type="text/markdown",
        category_label=None,
        chunk_id=uuid.uuid4(),
        excerpt="x",
        fused_score=fused,
    )


def _hit(document_id: uuid.UUID) -> SearchHit:
    return SearchHit(
        document_id=document_id,
        title="t",
        mime_type="text/markdown",
        category_label=None,
        chunk_id=uuid.uuid4(),
        excerpt="x",
        snippet="x",
        score=0.1,
        rerank_score=None,
    )


def test_document_ranks_keeps_the_earliest_chunk() -> None:
    """A document with several chunks in one pool ranks at its best one."""
    a, b = uuid.uuid4(), uuid.uuid4()
    ranks = _document_ranks([_row(a), _row(b), _row(a)])
    assert ranks == {a: 1, b: 2}


def test_provenance_separates_the_two_legs() -> None:
    """A document found by only one leg records None for the other.

    This is the distinction the result list destroys: absent from a leg and
    last in a leg look the same once the rankings are merged and truncated.
    """
    both, vector_only, bm25_only = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    explain = _build_explain(
        vector_rows=[_row(vector_only), _row(both)],
        bm25_rows=[_row(both, 4.5), _row(bm25_only, 1.2)],
        candidates=[
            _candidate(both, 0.9),
            _candidate(vector_only, 0.5),
            _candidate(bm25_only, 0.2),
        ],
        hits=[_hit(both), _hit(vector_only), _hit(bm25_only)],
    )
    assert explain.provenance[both].vector_rank == 2
    assert explain.provenance[both].bm25_rank == 1
    assert explain.provenance[both].bm25_score == 4.5

    assert explain.provenance[vector_only].vector_rank == 1
    assert explain.provenance[vector_only].bm25_rank is None
    assert explain.provenance[vector_only].bm25_score is None

    assert explain.provenance[bm25_only].vector_rank is None
    assert explain.provenance[bm25_only].bm25_rank == 2


def test_provenance_records_reranker_movement() -> None:
    """Fused rank and final rank differ when the reranker reorders."""
    top, demoted = uuid.uuid4(), uuid.uuid4()
    explain = _build_explain(
        vector_rows=[_row(demoted), _row(top)],
        bm25_rows=[],
        # Fusion preferred `demoted`; the reranker put `top` first.
        candidates=[_candidate(demoted, 0.9), _candidate(top, 0.8)],
        hits=[_hit(top), _hit(demoted)],
    )
    assert explain.provenance[top].fused_rank == 2
    assert explain.provenance[top].final_rank == 1
    assert explain.provenance[demoted].fused_rank == 1
    assert explain.provenance[demoted].final_rank == 2


def test_candidates_reach_past_the_returned_hits() -> None:
    """The fused ordering is kept whole, not truncated to what was shown.

    Answering "where did the document I expected actually rank" is the reason
    explain exists, and that answer normally lies outside the result list.
    """
    shown, buried = uuid.uuid4(), uuid.uuid4()
    explain = _build_explain(
        vector_rows=[_row(shown), _row(buried)],
        bm25_rows=[],
        candidates=[_candidate(shown, 0.9), _candidate(buried, 0.1)],
        hits=[_hit(shown)],
    )
    assert [doc for doc, _ in explain.candidates] == [shown, buried]
    assert buried not in explain.provenance
    assert explain.vector_pool[buried] == 2
