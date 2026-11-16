"""Self-hosted embedder bake-off on CPU against the hosted baseline.

Each open-weight candidate embeds the same chunks and queries as the
baseline, on CPU with a fixed thread count. Ranking and fusion mirror
``bake_off.py`` (exact cosine, doc-level best chunk, weighted RRF k=20,
3:1). Every candidate is compared to the baseline with the paired
clustered test, and its encode throughput is recorded, because a
candidate that cannot index or answer in reasonable time on the target
host is out regardless of quality.

Embeddings are cached under ``--cache`` so the reranker comparison can
reuse them. Run with:

    JINA_API_KEY=... uv run python evals/retrieval/local_bakeoff.py --threads 8

Omit ``JINA_API_KEY`` to skip the baseline (no paired comparison).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import numpy as np
from bake_off import embed_jina, ingest_text_only, normalise, rank_vector_memory
from eval_db import fresh_database
from metrics import clustered_paired_test, mrr, ndcg_at_k, recall_at_k
from run_eval import _primary_doc, load_golden, rank_bm25, rank_rrf

RRF_K = 20
VECTOR_WEIGHT = 3.0
BASELINE = "jina-v4 (hosted)"
CATEGORIES = ("keyword", "semantic", "decision", "numeric", "date", "english")


@dataclass(frozen=True)
class Candidate:
    name: str
    model_id: str
    # Name of the query prompt in the model's own config; None when the
    # model takes queries without an instruction.
    query_prompt: str | None


CANDIDATES = (
    Candidate("qwen3-0.6b", "Qwen/Qwen3-Embedding-0.6B", "query"),
    Candidate("bge-m3", "BAAI/bge-m3", None),
    Candidate("granite-311m-r2", "ibm-granite/granite-embedding-311m-multilingual-r2", None),
    Candidate("granite-97m-r2", "ibm-granite/granite-embedding-97m-multilingual-r2", None),
    Candidate("harrier-0.6b", "microsoft/harrier-oss-v1-0.6b", "web_search_query"),
    Candidate("harrier-270m", "microsoft/harrier-oss-v1-270m", "web_search_query"),
)


@dataclass
class Result:
    chunks: np.ndarray
    queries: np.ndarray
    chunks_per_second: float | None = None
    query_ms: float | None = None


def embed_local(candidate: Candidate, chunks: list[str], queries: list[str]) -> Result:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(candidate.model_id, device="cpu")
    model.max_seq_length = min(model.max_seq_length or 1024, 1024)

    model.encode(chunks[:4], batch_size=4)  # warm-up, excluded from timing
    start = time.perf_counter()
    chunk_vecs = model.encode(chunks, batch_size=8, show_progress_bar=False)
    chunk_seconds = time.perf_counter() - start

    kwargs = {"prompt_name": candidate.query_prompt} if candidate.query_prompt else {}
    # One query at a time: search embeds a single query per request.
    start = time.perf_counter()
    query_vecs = [model.encode([q], show_progress_bar=False, **kwargs)[0] for q in queries]
    query_seconds = time.perf_counter() - start

    return Result(
        chunks=normalise(np.asarray(chunk_vecs, dtype=np.float32)),
        queries=normalise(np.asarray(query_vecs, dtype=np.float32)),
        chunks_per_second=len(chunks) / chunk_seconds,
        query_ms=1000.0 * query_seconds / len(queries),
    )


def cached(cache: Path, name: str) -> Result | None:
    path = cache / f"{name}.npz"
    if not path.exists():
        return None
    data = np.load(path)
    speed = data["speed"]
    return Result(
        chunks=data["chunks"],
        queries=data["queries"],
        chunks_per_second=None if np.isnan(speed[0]) else float(speed[0]),
        query_ms=None if np.isnan(speed[1]) else float(speed[1]),
    )


def store(cache: Path, name: str, result: Result) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    speed = np.array(
        [
            np.nan if result.chunks_per_second is None else result.chunks_per_second,
            np.nan if result.query_ms is None else result.query_ms,
        ]
    )
    np.savez(cache / f"{name}.npz", chunks=result.chunks, queries=result.queries, speed=speed)


def score(result: Result, golden, bm25_ranked, chunk_doc, doc_titles, config: str):
    """Per-query (nDCG@10, MRR, recall@10) for one ranking configuration."""
    rows = []
    for i, q in enumerate(golden):
        judgments = {rel["doc"]: rel["grade"] for rel in q["relevant"]}
        vec = rank_vector_memory(result.queries[i], result.chunks, chunk_doc, doc_titles)
        ranked = (
            vec if config == "vector" else rank_rrf(vec, bm25_ranked[i], RRF_K, VECTOR_WEIGHT, 1.0)
        )
        rows.append(
            (
                ndcg_at_k(ranked, judgments, 10),
                mrr(ranked, judgments),
                recall_at_k(ranked, judgments, 10),
            )
        )
    return rows


def fmt(value: float | None, spec: str = ".3f") -> str:
    return "-" if value is None or np.isnan(value) else format(value, spec)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--cache", type=Path, default=HERE / ".bakeoff-cache")
    parser.add_argument("--only", nargs="*", default=None, help="Candidate names to run.")
    args = parser.parse_args()

    import torch

    torch.set_num_threads(args.threads)

    golden = [q for q in load_golden() if q["category"] != "no_answer"]
    queries = [q["query"] for q in golden]
    clusters = [_primary_doc(q) for q in golden]

    async with fresh_database() as pool:
        doc_titles, chunk_texts, chunk_doc = await ingest_text_only(pool)
        bm25_ranked = [await rank_bm25(pool, q) for q in queries]

    results: dict[str, Result] = {}
    if os.environ.get("JINA_API_KEY"):
        hit = cached(args.cache, "jina-v4")
        if hit is None:
            print("embedding: jina v4 (hosted) ...", flush=True)
            hit = Result(
                chunks=await embed_jina("jina-embeddings-v4", 2048, chunk_texts, "passage"),
                queries=await embed_jina("jina-embeddings-v4", 2048, queries, "query"),
            )
            store(args.cache, "jina-v4", hit)
        results[BASELINE] = hit

    for candidate in CANDIDATES:
        if args.only and candidate.name not in args.only:
            continue
        hit = cached(args.cache, candidate.name)
        if hit is None:
            print(f"embedding: {candidate.name} ...", flush=True)
            hit = embed_local(candidate, chunk_texts, queries)
            store(args.cache, candidate.name, hit)
        results[candidate.name] = hit

    lines = [
        f"# Self-hosted embedder bake-off — {len(golden)} queries, "
        f"{len(chunk_texts)} chunks, CPU {args.threads} threads",
        "",
        "Fused = weighted RRF with the shared stemmed BM25 leg. The difference "
        "column is fused nDCG@10 minus the baseline's, with a clustered "
        "bootstrap interval and a permutation p-value.",
        "",
        "| model | dims | vector nDCG@10 | fused nDCG@10 | fused MRR | fused recall@10 |"
        " diff vs baseline [95% CI] | p | chunks/s | query ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    category_lines = [
        "",
        "## Fused nDCG@10 by category",
        "",
        "| model | " + " | ".join(CATEGORIES) + " |",
        "|---|" + "---|" * len(CATEGORIES),
    ]

    baseline_rows = (
        score(results[BASELINE], golden, bm25_ranked, chunk_doc, doc_titles, "rrf")
        if BASELINE in results
        else None
    )
    for name, result in results.items():
        vector = score(result, golden, bm25_ranked, chunk_doc, doc_titles, "vector")
        fused = score(result, golden, bm25_ranked, chunk_doc, doc_titles, "rrf")
        fused_ndcg = [row[0] for row in fused]
        diff, p_text = "-", "-"
        if baseline_rows is not None and name != BASELINE:
            mean, low, high, p = clustered_paired_test(
                fused_ndcg, [row[0] for row in baseline_rows], clusters
            )
            diff = f"{mean:+.3f} [{fmt(low, '+.3f')}, {fmt(high, '+.3f')}]"
            p_text = f"{p:.3f}"
        lines.append(
            f"| {name} | {result.chunks.shape[1]} | {np.mean([r[0] for r in vector]):.3f} |"
            f" {np.mean(fused_ndcg):.3f} | {np.mean([r[1] for r in fused]):.3f} |"
            f" {np.mean([r[2] for r in fused]):.3f} | {diff} | {p_text} |"
            f" {fmt(result.chunks_per_second, '.1f')} | {fmt(result.query_ms, '.0f')} |"
        )
        by_category: dict[str, list[float]] = defaultdict(list)
        for q, value in zip(golden, fused_ndcg, strict=True):
            by_category[q["category"]].append(value)
        category_lines.append(
            f"| {name} | "
            + " | ".join(
                f"{np.mean(by_category[c]):.3f}" if by_category[c] else "-" for c in CATEGORIES
            )
            + " |"
        )

    report = "\n".join(lines + category_lines)
    print("\n" + report)
    out = HERE / "reports" / "experiment-local-embedder-bakeoff.md"
    out.write_text(report + "\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    asyncio.run(main())
