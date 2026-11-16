"""Self-hosted reranker comparison over cached bake-off embeddings.

For each embedder cached by ``local_bakeoff.py`` and each reranker
(open-weight cross-encoders on CPU, plus the hosted baseline when
``JINA_API_KEY`` is set), rerank the fused top-N and measure against
the golden set. Each row is compared with a reference configuration
(by default the hosted embedder + hosted reranker) using the paired
clustered test.

Candidate documents are represented by their best chunk for that
embedder, as in ``rerank_off.py``.

    JINA_API_KEY=... uv run python evals/retrieval/local_rerank_off.py

Configurations are named ``<embedder> + <reranker> @<candidates>``.
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
import numpy as np
from bake_off import ingest_text_only, rank_vector_memory
from eval_db import fresh_database
from host_benchmark import (
    RERANK_BATCH,
    RERANK_MAX_TOKENS,
    RERANKERS,
    _qwen_pair,
)
from local_bakeoff import cached
from metrics import clustered_paired_test, mrr, ndcg_at_k
from rerank_off import best_chunk_per_doc, rerank_api
from run_eval import _primary_doc, load_golden, rank_bm25, rank_rrf

RRF_K = 20
VECTOR_WEIGHT = 3.0
HOSTED_RERANKER = "jina-reranker-v3"
HOSTED_EMBEDDER = "jina-v4"
DEFAULT_EMBEDDERS = (HOSTED_EMBEDDER, "harrier-0.6b", "qwen3-0.6b", "granite-311m-r2", "bge-m3")
DEFAULT_REFERENCE = f"{HOSTED_EMBEDDER} + {HOSTED_RERANKER} @20"

# Per embedder: fused ranking per query, and best-chunk excerpt per document per query.
Prepared = dict[str, tuple[list[list[str]], list[dict[int, str]]]]
Configs = dict[str, list[list[str]]]


def local_order(model, name: str, query: str, docs: list[str]) -> list[int]:
    by_length = sorted(range(len(docs)), key=lambda i: len(docs[i]))
    if name.startswith("qwen3"):
        pairs = [_qwen_pair(query, docs[i]) for i in by_length]
    else:
        pairs = [[query, docs[i]] for i in by_length]
    scores = model.predict(pairs, batch_size=RERANK_BATCH, show_progress_bar=False)
    return [by_length[int(i)] for i in np.argsort(-np.asarray(scores))]


def write_report(
    configs: Configs,
    judgments: list[dict[str, int]],
    clusters: list[str],
    reference_name: str,
    out_path: Path,
) -> None:
    def ndcgs(ranked_lists: list[list[str]]) -> list[float]:
        return [ndcg_at_k(r, j, 10) for r, j in zip(ranked_lists, judgments, strict=True)]

    reference = ndcgs(configs[reference_name]) if reference_name in configs else None

    lines = [
        f"# Self-hosted reranker comparison — {len(judgments)} queries",
        "",
        "Rows are `embedder + reranker @candidates`: the reranker reorders that many "
        f"top fused results. Difference is nDCG@10 minus `{reference_name}`, with a "
        "clustered bootstrap interval and a permutation p-value.",
        "",
        "| configuration | nDCG@10 | MRR | diff vs reference [95% CI] | p |",
        "|---|---|---|---|---|",
    ]
    for name, ranked_lists in configs.items():
        scores = ndcgs(ranked_lists)
        mrrs = [mrr(r, j) for r, j in zip(ranked_lists, judgments, strict=True)]
        diff, p_text = "-", "-"
        if reference is not None and name != reference_name:
            mean, low, high, p = clustered_paired_test(scores, reference, clusters)
            diff = f"{mean:+.3f} [{low:+.3f}, {high:+.3f}]"
            p_text = f"{p:.3f}"
        lines.append(
            f"| {name} | {np.mean(scores):.3f} | {np.mean(mrrs):.3f} | {diff} | {p_text} |"
        )

    report = "\n".join(lines)
    print("\n" + report)
    out_path.write_text(report + "\n")
    print(f"\nwritten to {out_path}")


def rerank_inputs(
    prepared: Prepared, title_to_index: dict[str, int], emb: str, i: int, count: int
) -> tuple[list[str], list[str]]:
    fused, excerpts = prepared[emb]
    candidates = fused[i][:count]
    return candidates, [excerpts[i][title_to_index[t]] for t in candidates]


async def rerank_hosted(
    prepared: Prepared, title_to_index: dict[str, int], queries: list[str], counts: list[int]
) -> Configs:
    configs: Configs = {}
    async with httpx.AsyncClient() as client:
        for emb in prepared:
            for count in counts:
                print(f"reranking {emb} with {HOSTED_RERANKER} @{count} ...", flush=True)
                out = []
                for i, query in enumerate(queries):
                    candidates, texts = rerank_inputs(prepared, title_to_index, emb, i, count)
                    order = await rerank_api(client, HOSTED_RERANKER, query, texts)
                    await asyncio.sleep(0.5)
                    out.append([candidates[j] for j in order] + prepared[emb][0][i][count:])
                configs[f"{emb} + {HOSTED_RERANKER} @{count}"] = out
    return configs


def rerank_local(
    prepared: Prepared,
    title_to_index: dict[str, int],
    queries: list[str],
    counts: list[int],
    rerankers: list[str],
) -> Configs:
    from sentence_transformers import CrossEncoder

    configs: Configs = {}
    for name in rerankers:
        model = CrossEncoder(RERANKERS[name], device="cpu", max_length=RERANK_MAX_TOKENS)
        for emb in prepared:
            for count in counts:
                print(f"reranking {emb} with {name} @{count} ...", flush=True)
                out = []
                for i, query in enumerate(queries):
                    candidates, texts = rerank_inputs(prepared, title_to_index, emb, i, count)
                    order = local_order(model, name, query, texts)
                    out.append([candidates[j] for j in order] + prepared[emb][0][i][count:])
                configs[f"{emb} + {name} @{count}"] = out
        del model
    return configs


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=HERE / ".bakeoff-cache")
    parser.add_argument("--embedders", nargs="*", default=list(DEFAULT_EMBEDDERS))
    parser.add_argument("--rerankers", nargs="*", default=list(RERANKERS), choices=list(RERANKERS))
    parser.add_argument("--candidates", nargs="*", type=int, default=[20])
    parser.add_argument("--reference", default=DEFAULT_REFERENCE)
    parser.add_argument(
        "--report", type=Path, default=HERE / "reports" / "experiment-local-reranker-pairs.md"
    )
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()

    import torch

    torch.set_num_threads(args.threads)

    golden = [q for q in load_golden() if q["category"] != "no_answer"]
    queries = [q["query"] for q in golden]
    clusters = [_primary_doc(q) for q in golden]
    judgments = [{rel["doc"]: rel["grade"] for rel in q["relevant"]} for q in golden]

    async with fresh_database() as pool:
        doc_titles, chunk_texts, chunk_doc = await ingest_text_only(pool)
        bm25_ranked = [await rank_bm25(pool, q) for q in queries]
    title_to_index = {t: i for i, t in enumerate(doc_titles)}

    prepared: Prepared = {}
    for emb in args.embedders:
        result = cached(args.cache, emb)
        if result is None:
            print(f"skipping {emb}: not in cache", flush=True)
            continue
        fused, excerpts = [], []
        for i in range(len(golden)):
            vec = rank_vector_memory(result.queries[i], result.chunks, chunk_doc, doc_titles)
            fused.append(rank_rrf(vec, bm25_ranked[i], RRF_K, VECTOR_WEIGHT, 1.0))
            excerpts.append(
                best_chunk_per_doc(result.queries[i], result.chunks, chunk_doc, chunk_texts)
            )
        prepared[emb] = (fused, excerpts)

    configs: Configs = {f"{emb} + none": fused for emb, (fused, _) in prepared.items()}
    if os.environ.get("JINA_API_KEY"):
        configs |= await rerank_hosted(prepared, title_to_index, queries, args.candidates)
    configs |= rerank_local(prepared, title_to_index, queries, args.candidates, args.rerankers)

    write_report(configs, judgments, clusters, args.reference, args.report)


if __name__ == "__main__":
    asyncio.run(main())
