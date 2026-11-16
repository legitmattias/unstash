"""Measure self-hosted inference cost on a target host: speed and memory.

Standalone on purpose: it depends only on ``sentence-transformers`` so it
can run in a throwaway container on the host being sized, without the
application installed. Each model is measured in its own process so peak
memory is attributable to that model alone.

Input is a JSON file ``{"chunks": [...], "queries": [...]}``. Produce it
from the evaluation corpus with ``--dump``, which does need the
application package:

    uv run python evals/retrieval/host_benchmark.py --dump texts.json

Then, on the host:

    python host_benchmark.py texts.json --threads 8

Prints one JSON object per model.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

EMBEDDERS = {
    "qwen3-0.6b": ("Qwen/Qwen3-Embedding-0.6B", "query"),
    "harrier-0.6b": ("microsoft/harrier-oss-v1-0.6b", "web_search_query"),
    "harrier-270m": ("microsoft/harrier-oss-v1-270m", "web_search_query"),
    "bge-m3": ("BAAI/bge-m3", None),
    "granite-311m-r2": ("ibm-granite/granite-embedding-311m-multilingual-r2", None),
}
RERANKERS = {
    "bge-reranker-v2-m3": "BAAI/bge-reranker-v2-m3",
    "qwen3-reranker-0.6b": "tomaarsen/Qwen3-Reranker-0.6B-seq-cls",
    "mmarco-mminilm-l12": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
}
# Tokens per query-document pair. The smallest reranker accepts 512.
RERANK_MAX_TOKENS = 512
# Candidates are scored in small length-sorted batches so short texts
# are not padded to the longest candidate.
RERANK_BATCH = 4
RERANK_CANDIDATES = 20
RERANK_QUERIES = 10
MAX_TOKENS = 1024


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def bench_embedder(name: str, chunks: list[str], queries: list[str]) -> dict[str, object]:
    from sentence_transformers import SentenceTransformer

    model_id, prompt = EMBEDDERS[name]
    start = time.perf_counter()
    model = SentenceTransformer(model_id, device="cpu")
    model.max_seq_length = min(model.max_seq_length or MAX_TOKENS, MAX_TOKENS)
    load_seconds = time.perf_counter() - start

    model.encode(chunks[:4], batch_size=4)
    start = time.perf_counter()
    model.encode(chunks, batch_size=8, show_progress_bar=False)
    chunk_seconds = time.perf_counter() - start

    kwargs = {"prompt_name": prompt} if prompt else {}
    timings = []
    for query in queries:
        start = time.perf_counter()
        model.encode([query], show_progress_bar=False, **kwargs)
        timings.append(1000.0 * (time.perf_counter() - start))

    return {
        "kind": "embedder",
        "model": name,
        "load_s": round(load_seconds, 1),
        "chunks_per_s": round(len(chunks) / chunk_seconds, 2),
        "query_ms_p50": round(_percentile(timings, 0.5)),
        "query_ms_p95": round(_percentile(timings, 0.95)),
        "peak_rss_mb": round(_peak_rss_mb()),
    }


def _qwen_pair(query: str, document: str) -> list[str]:
    prefix = (
        "<|im_start|>system\nJudge whether the Document meets the requirements based on "
        'the Query and the Instruct provided. Note that the answer can only be "yes" or '
        '"no".<|im_end|>\n<|im_start|>user\n'
    )
    instruction = "Given a web search query, retrieve relevant passages that answer the query"
    suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    return [
        f"{prefix}<Instruct>: {instruction}\n<Query>: {query}\n",
        f"<Document>: {document}{suffix}",
    ]


def bench_reranker(name: str, chunks: list[str], queries: list[str]) -> dict[str, object]:
    from sentence_transformers import CrossEncoder

    start = time.perf_counter()
    model = CrossEncoder(RERANKERS[name], device="cpu", max_length=RERANK_MAX_TOKENS)
    load_seconds = time.perf_counter() - start

    def pairs(index: int) -> list[list[str]]:
        query = queries[index % len(queries)]
        offset = (index * RERANK_CANDIDATES) % max(1, len(chunks) - RERANK_CANDIDATES)
        docs = sorted(chunks[offset : offset + RERANK_CANDIDATES], key=len)
        if name.startswith("qwen3"):
            return [_qwen_pair(query, doc) for doc in docs]
        return [[query, doc] for doc in docs]

    model.predict(pairs(0)[:2])
    timings = []
    for index in range(RERANK_QUERIES):
        start = time.perf_counter()
        model.predict(pairs(index), batch_size=RERANK_BATCH)
        timings.append(1000.0 * (time.perf_counter() - start))

    return {
        "kind": "reranker",
        "model": name,
        "candidates": RERANK_CANDIDATES,
        "load_s": round(load_seconds, 1),
        "rerank_ms_p50": round(_percentile(timings, 0.5)),
        "rerank_ms_p95": round(_percentile(timings, 0.95)),
        "peak_rss_mb": round(_peak_rss_mb()),
    }


def dump_texts(target: Path) -> None:
    here = Path(__file__).parent
    sys.path.insert(0, str(here))
    sys.path.insert(0, str(here.parents[1] / "src"))
    from run_eval import load_golden

    from unstash.documents.parser import parse_to_chunks

    chunks = [
        chunk.text
        for path in sorted((here / "corpus").glob("*.md"))
        for chunk in parse_to_chunks(path).chunks
    ]
    queries = [q["query"] for q in load_golden() if q["category"] != "no_answer"]
    target.write_text(json.dumps({"chunks": chunks, "queries": queries}, ensure_ascii=False))
    print(f"wrote {len(chunks)} chunks, {len(queries)} queries to {target}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("texts", type=Path, nargs="?")
    parser.add_argument("--dump", type=Path, default=None)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--one", default=None, help="Internal: measure one model in-process.")
    args = parser.parse_args()

    if args.dump is not None:
        dump_texts(args.dump)
        return
    if args.texts is None:
        parser.error("texts file is required unless --dump is given")

    if args.one is None:
        for name in [*EMBEDDERS, *RERANKERS]:
            if args.only and name not in args.only:
                continue
            subprocess.run(  # noqa: S603 — re-invokes this script with fixed arguments
                [
                    sys.executable,
                    __file__,
                    str(args.texts),
                    "--threads",
                    str(args.threads),
                    "--one",
                    name,
                ],
                check=False,
            )
        return

    import torch

    torch.set_num_threads(args.threads)
    data = json.loads(args.texts.read_text())
    bench = bench_embedder if args.one in EMBEDDERS else bench_reranker
    result = bench(args.one, data["chunks"], data["queries"])
    result["threads"] = args.threads
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
