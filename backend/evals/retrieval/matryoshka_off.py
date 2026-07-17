"""E.2 — Matryoshka truncation experiment for Jina v4.

Jina v4 is Matryoshka-trained: the first N dimensions form a valid
lower-dimensional embedding. DiskANN can index only those N dims
(`num_dimensions`), shrinking index memory. This measures what truncation
to 1024/512 costs against full 2048, on vector-only and fused ranking.

One embedding pass at 2048; truncation is a local slice + renormalise,
identical to what the index-side truncation computes.

    JINA_API_KEY=... python evals/retrieval/matryoshka_off.py
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import numpy as np  # noqa: E402
from bake_off import embed_jina, ingest_text_only, normalise, rank_vector_memory  # noqa: E402
from eval_db import fresh_database  # noqa: E402
from metrics import mrr, ndcg_at_k, recall_at_k  # noqa: E402
from run_eval import load_golden, rank_bm25, rank_rrf  # noqa: E402

RRF_K = 20
VECTOR_WEIGHT = 3.0
DIMS = (2048, 1024, 512)


async def main() -> None:
    golden = [q for q in load_golden() if q["category"] != "no_answer"]
    queries = [q["query"] for q in golden]

    async with fresh_database() as pool:
        doc_titles, chunk_texts, chunk_doc = await ingest_text_only(pool)
        bm25_ranked = [await rank_bm25(pool, q) for q in queries]

    chunk_full = await embed_jina("jina-embeddings-v4", 2048, chunk_texts, "passage")
    query_full = await embed_jina("jina-embeddings-v4", 2048, queries, "query")

    lines = [f"# E.2 Matryoshka truncation — jina-v4, {len(golden)} queries", ""]
    lines.append(
        "| dims / config | all nDCG@10 | all MRR | all recall@10 |"
        " keyword | semantic | decision | english |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")

    for dims in DIMS:
        chunk_matrix = normalise(chunk_full[:, :dims])
        query_matrix = normalise(query_full[:, :dims])
        for config in ("vector", "rrf"):
            overall, mrrs, recalls = [], [], []
            cats: dict[str, list[float]] = defaultdict(list)
            for i, q in enumerate(golden):
                judgments = {rel["doc"]: rel["grade"] for rel in q["relevant"]}
                vec = rank_vector_memory(query_matrix[i], chunk_matrix, chunk_doc, doc_titles)
                ranked = vec if config == "vector" else rank_rrf(
                    vec, bm25_ranked[i], RRF_K, VECTOR_WEIGHT, 1.0
                )
                overall.append(ndcg_at_k(ranked, judgments, 10))
                mrrs.append(mrr(ranked, judgments))
                recalls.append(recall_at_k(ranked, judgments, 10))
                cats[q["category"]].append(ndcg_at_k(ranked, judgments, 10))

            def avg(values: list[float]) -> str:
                return f"{sum(values) / len(values):.3f}" if values else "-"

            lines.append(
                f"| {dims} {config} | {avg(overall)} | {avg(mrrs)} | {avg(recalls)} |"
                f" {avg(cats['keyword'])} | {avg(cats['semantic'])} |"
                f" {avg(cats['decision'])} | {avg(cats['english'])} |"
            )

    report = "\n".join(lines)
    print("\n" + report)
    out = HERE / "reports" / "experiment-e2-matryoshka.md"
    out.write_text(report + "\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    asyncio.run(main())
