"""Refusal-path measurement over the no_answer slice (issue #149).

The product always returns top-k results; abstention would mean hiding
them below a score threshold. This script measures whether such a
threshold exists: for every golden query — answerable and no_answer —
it runs the production retrieval shape (weighted RRF over vector + BM25,
top candidates reranked with jina-reranker-v3) and records the **top
result's rerank score**. It then sweeps thresholds and reports the
operating points: how many no_answer queries would correctly show
nothing vs how many answerable queries would wrongly show nothing.

Reported separately from the ranking metrics, per the golden-set
discipline (no_answer stays out of Recall/MRR/nDCG).

    JINA_API_KEY=... python evals/retrieval/refusal_check.py
    python evals/retrieval/refusal_check.py --embedder fake  # plumbing smoke;
        scores come from RRF fusion instead of the reranker and mean nothing.

The no_answer slice is 4 queries — descriptive numbers only, no intervals.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import httpx
from eval_db import fresh_database
from run_eval import (
    build_embedder,
    ingest_corpus,
    load_golden,
    rank_bm25,
    rank_rrf,
    rank_vector,
)

RERANK_MODEL = "jina-reranker-v3"
RERANK_CANDIDATES = 10


async def best_excerpts(pool, query_vec: str, titles: list[str]) -> list[str]:
    """The best-matching chunk text per document, mirroring production rerank input."""
    excerpts = []
    for title in titles:
        row = await pool.fetchrow(
            "SELECT c.text FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE d.title = $1 ORDER BY c.embedding <=> $2::vector LIMIT 1",
            title,
            query_vec,
        )
        excerpts.append(row["text"])
    return excerpts


async def top_rerank_score(client: httpx.AsyncClient, query: str, excerpts: list[str]) -> float:
    response = await client.post(
        "https://api.jina.ai/v1/rerank",
        json={"model": RERANK_MODEL, "query": query, "documents": excerpts},
        headers={"Authorization": f"Bearer {os.environ['JINA_API_KEY']}"},
        timeout=60.0,
    )
    response.raise_for_status()
    results = response.json()["results"]
    return max(float(r["relevance_score"]) for r in results)


async def run(embedder_kind: str) -> None:
    from unstash.documents.embedder import EmbeddingTask

    use_reranker = embedder_kind != "fake"
    if use_reranker and not os.environ.get("JINA_API_KEY"):
        sys.exit("JINA_API_KEY is required (or use --embedder fake for a plumbing smoke)")

    golden = load_golden()
    embedder = build_embedder(embedder_kind)

    rows: list[tuple[str, str, float]] = []  # (query id, category, top score)
    async with fresh_database() as pool:
        await ingest_corpus(pool, embedder)
        async with httpx.AsyncClient() as client:
            for q in golden:
                batch = await embedder.embed([q["query"]], task=EmbeddingTask.QUERY)
                qvec = "[" + ",".join(str(v) for v in batch.vectors[0]) + "]"
                fused = rank_rrf(await rank_vector(pool, qvec), await rank_bm25(pool, q["query"]))
                top = fused[:RERANK_CANDIDATES]
                if not top:
                    rows.append((q["id"], q["category"], float("-inf")))
                    continue
                if use_reranker:
                    excerpts = await best_excerpts(pool, qvec, top)
                    score = await top_rerank_score(client, q["query"], excerpts)
                else:
                    # Fake mode: RRF score of the top document (plumbing only).
                    score = 1.0 / (20 + 1) * 4.0
                rows.append((q["id"], q["category"], score))

    no_answer = sorted((s for _, c, s in rows if c == "no_answer"), reverse=True)
    answerable = sorted((s for _, c, s in rows if c != "no_answer"), reverse=True)

    print("\nper-query top scores (no_answer slice):")
    for qid, category, score in rows:
        if category == "no_answer":
            print(f"  {qid}  {score:.4f}")
    print("\nanswerable top-score distribution:")
    print(
        f"  n={len(answerable)}  min={min(answerable):.4f}"
        f"  p25={answerable[int(len(answerable) * 0.75)]:.4f}"
        f"  median={answerable[len(answerable) // 2]:.4f}"
    )

    print("\nthreshold sweep (abstain when top score < t):")
    print("  t        no_answer refused    answerable wrongly refused")
    candidates = sorted(set(no_answer + answerable))
    for t in candidates:
        refused = sum(1 for s in no_answer if s < t)
        false_refused = sum(1 for s in answerable if s < t)
        print(
            f"  {t:.4f}   {refused}/{len(no_answer)}                  {false_refused}/{len(answerable)}"
        )
        if refused == len(no_answer):
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", choices=["jina", "fake"], default="jina")
    args = parser.parse_args()
    asyncio.run(run(args.embedder))
