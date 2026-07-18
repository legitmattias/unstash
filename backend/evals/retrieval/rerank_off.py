"""Joint reranker × embedder experiment.

For each embedder (jina-v4, jina-v5-text-small) and each reranker
(jina-reranker-v2-base-multilingual, jina-reranker-v3), rerank the fused
top-20 candidates (weighted RRF k=20, 3:1) and measure against the golden
set. Decides the embedder and reranker choice together: the question is
which *pair* wins — especially on the decision slice, where v5's
retrieval is weak but its recall hands the reranker more correct
candidates.

Each candidate document is represented to the reranker by its best chunk
(by that embedder's cosine), mirroring how the production endpoint will
rerank chunk excerpts.

    JINA_API_KEY=... python evals/retrieval/rerank_off.py
"""

from __future__ import annotations

import asyncio
import os
import ssl
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
from bake_off import embed_jina, ingest_text_only, rank_vector_memory  # noqa: E402
from eval_db import fresh_database  # noqa: E402
from metrics import mrr, ndcg_at_k  # noqa: E402
from run_eval import load_golden, rank_bm25, rank_rrf  # noqa: E402

RRF_K = 20
VECTOR_WEIGHT = 3.0
RERANK_CANDIDATES = 20

EMBEDDERS = {
    "jina-v4": ("jina-embeddings-v4", 2048),
    "jina-v5-small": ("jina-embeddings-v5-text-small", 1024),
}
RERANKERS = ["jina-reranker-v2-base-multilingual", "jina-reranker-v3"]


async def rerank_api(
    client: httpx.AsyncClient, model: str, query: str, docs: list[str]
) -> list[int]:
    """Return candidate indices in reranked order; backs off on 429/5xx and transport errors."""
    for attempt in range(6):
        try:
            r = await client.post(
                "https://api.jina.ai/v1/rerank",
                json={
                    "model": model,
                    "query": query,
                    "documents": docs,
                    "top_n": len(docs),
                    "return_documents": False,
                },
                headers={"Authorization": f"Bearer {os.environ['JINA_API_KEY']}"},
                timeout=60,
            )
        except (httpx.HTTPError, ssl.SSLError):
            if attempt < 5:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            raise
        if r.status_code in (429, 500, 502, 503) and attempt < 5:
            await asyncio.sleep(2.0 * (attempt + 1))
            continue
        r.raise_for_status()
        return [item["index"] for item in r.json()["results"]]
    raise RuntimeError("unreachable")


async def embed_with_retry(model: str, dims: int, texts: list[str], task: str) -> np.ndarray:
    for attempt in range(4):
        try:
            return await embed_jina(model, dims, texts, task)
        except (httpx.HTTPError, ssl.SSLError):
            if attempt == 3:
                raise
            await asyncio.sleep(3.0 * (attempt + 1))
    raise RuntimeError("unreachable")


def best_chunk_per_doc(
    query_vec: np.ndarray,
    chunk_matrix: np.ndarray,
    chunk_doc: list[int],
    chunk_texts: list[str],
) -> dict[int, str]:
    sims = chunk_matrix @ query_vec
    best: dict[int, tuple[float, str]] = {}
    for idx, sim in enumerate(sims):
        d = chunk_doc[idx]
        if d not in best or sim > best[d][0]:
            best[d] = (float(sim), chunk_texts[idx])
    return {d: text for d, (_, text) in best.items()}


async def main() -> None:
    golden = [q for q in load_golden() if q["category"] != "no_answer"]
    queries = [q["query"] for q in golden]

    async with fresh_database() as pool:
        doc_titles, chunk_texts, chunk_doc = await ingest_text_only(pool)
        bm25_ranked = [await rank_bm25(pool, q) for q in queries]

    title_to_index = {t: i for i, t in enumerate(doc_titles)}
    lines = [f"# Reranker × embedder — {len(golden)} queries, fused top-{RERANK_CANDIDATES}", ""]
    lines.append("| pair | all nDCG@10 | all MRR | keyword | semantic | decision | english |")
    lines.append("|---|---|---|---|---|---|---|")

    async with httpx.AsyncClient() as client:
        for emb_name, (model, dims) in EMBEDDERS.items():
            print(f"embedding with {emb_name} ...", flush=True)
            chunk_matrix = await embed_with_retry(model, dims, chunk_texts, "passage")
            query_matrix = await embed_with_retry(model, dims, queries, "query")

            fused: list[list[str]] = []
            doc_excerpts: list[dict[int, str]] = []
            for i in range(len(golden)):
                vec = rank_vector_memory(query_matrix[i], chunk_matrix, chunk_doc, doc_titles)
                fused.append(rank_rrf(vec, bm25_ranked[i], RRF_K, VECTOR_WEIGHT, 1.0))
                doc_excerpts.append(
                    best_chunk_per_doc(query_matrix[i], chunk_matrix, chunk_doc, chunk_texts)
                )

            configs: dict[str, list[list[str]]] = {f"{emb_name} + no rerank": fused}
            for rr in RERANKERS:
                print(f"  reranking with {rr} ...", flush=True)
                reranked_all: list[list[str]] = []
                for i, q in enumerate(golden):
                    candidates = fused[i][:RERANK_CANDIDATES]
                    texts = [doc_excerpts[i][title_to_index[t]] for t in candidates]
                    order = await rerank_api(client, rr, q["query"], texts)
                    await asyncio.sleep(0.5)
                    reranked = [candidates[j] for j in order] + fused[i][RERANK_CANDIDATES:]
                    reranked_all.append(reranked)
                configs[f"{emb_name} + {rr.replace('jina-reranker-', '')}"] = reranked_all

            for name, ranked_lists in configs.items():
                overall, mrrs = [], []
                cats: dict[str, list[float]] = defaultdict(list)
                for i, q in enumerate(golden):
                    judgments = {rel["doc"]: rel["grade"] for rel in q["relevant"]}
                    overall.append(ndcg_at_k(ranked_lists[i], judgments, 10))
                    mrrs.append(mrr(ranked_lists[i], judgments))
                    cats[q["category"]].append(ndcg_at_k(ranked_lists[i], judgments, 10))

                def avg(values: list[float]) -> str:
                    return f"{sum(values) / len(values):.3f}" if values else "-"

                lines.append(
                    f"| {name} | {avg(overall)} | {avg(mrrs)} | {avg(cats['keyword'])} |"
                    f" {avg(cats['semantic'])} | {avg(cats['decision'])} | {avg(cats['english'])} |"
                )

    report = "\n".join(lines)
    print("\n" + report)
    out = HERE / "reports" / "experiment-reranker-pairs.md"
    out.write_text(report + "\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    asyncio.run(main())
