"""Retrieval evaluation runner.

Ingests the synthetic corpus into a fresh Postgres (testcontainer, schema
at migration head), embeds it, runs each retrieval configuration over the
golden queries, and reports Recall@10, MRR, and nDCG@10 per category.

Configurations: vector-only, bm25-only, rrf (fusion in Python here; the
production search endpoint implements the same math in SQL).

Judgments are document-level, so chunk hits are reduced to documents by
best-chunk score before metrics.

Usage:
    python run_eval.py --embedder fake            # plumbing check, meaningless numbers
    JINA_API_KEY=... python run_eval.py           # the real measurement
    ... --report reports/baseline.md              # also write a markdown report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import asyncpg
from metrics import (
    MIN_CLUSTERS_FOR_CI,
    clustered_bootstrap_ci,
    clustered_paired_test,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

# Adopted production fusion settings (mirror config.py search_rrf_k /
# search_vector_weight) so a re-run's ``rrf`` config is comparable to
# what the search endpoint serves.
RRF_K = 20
VECTOR_WEIGHT = 3.0
TOP_N = 50


def load_golden() -> list[dict]:
    lines = (HERE / "golden.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def build_embedder(kind: str):
    from unstash.documents.embedder import FakeEmbedder
    from unstash.inference.jina import JinaEmbedder

    if kind == "fake":
        return FakeEmbedder(dimensions=2048)
    return JinaEmbedder(
        api_key=os.environ["JINA_API_KEY"],
        base_url="https://api.jina.ai/v1",
        model="jina-embeddings-v4",
        dimensions=2048,
        timeout=60.0,
    )


async def ingest_corpus(pool: asyncpg.Pool, embedder) -> None:
    from unstash.documents.embedder import EmbeddingTask
    from unstash.documents.parser import parse_to_chunks

    org_id = await pool.fetchval(
        "INSERT INTO organisations (slug, name) VALUES ('eval', 'Eval Org') RETURNING id"
    )
    files = sorted((HERE / "corpus").glob("*.md"))
    print(f"ingesting {len(files)} documents ...", flush=True)
    for path in files:
        parsed = parse_to_chunks(path)
        texts = [c.text for c in parsed.chunks]
        batch = await embedder.embed(texts, task=EmbeddingTask.PASSAGE)
        doc_id = await pool.fetchval(
            "INSERT INTO documents (org_id, title, source_uri, mime_type, size_bytes,"
            " content_hash, status) VALUES ($1, $2, $3, 'text/markdown', $4, $5, 'indexed')"
            " RETURNING id",
            org_id,
            path.name,
            str(path),
            path.stat().st_size,
            path.name,
        )
        for chunk, vector in zip(parsed.chunks, batch.vectors, strict=True):
            await pool.execute(
                "INSERT INTO chunks (org_id, document_id, chunk_index, text, token_count,"
                " char_offset_start, char_offset_end, embedding)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)",
                org_id,
                doc_id,
                chunk.chunk_index,
                chunk.text,
                chunk.token_count,
                chunk.char_offset_start,
                chunk.char_offset_end,
                "[" + ",".join(str(v) for v in vector) + "]",
            )
    total = await pool.fetchval("SELECT count(*) FROM chunks")
    print(f"ingested; {total} chunks", flush=True)


async def rank_vector(pool: asyncpg.Pool, query_vec: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT d.title, MIN(c.embedding <=> $1::vector) AS dist"
        " FROM chunks c JOIN documents d ON d.id = c.document_id"
        " GROUP BY d.title ORDER BY dist LIMIT $2",
        query_vec,
        TOP_N,
    )
    return [r["title"] for r in rows]


async def rank_bm25(pool: asyncpg.Pool, query_text: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT d.title, MAX(paradedb.score(c.id)) AS score"
        " FROM chunks c JOIN documents d ON d.id = c.document_id"
        " WHERE c.text @@@ $1"
        " GROUP BY d.title ORDER BY score DESC LIMIT $2",
        query_text,
        TOP_N,
    )
    return [r["title"] for r in rows]


def rank_rrf(
    vector_ranked: list[str],
    bm25_ranked: list[str],
    k: int = RRF_K,
    vector_weight: float = VECTOR_WEIGHT,
    bm25_weight: float = 1.0,
) -> list[str]:
    scores: dict[str, float] = defaultdict(float)
    for ranked, weight in ((vector_ranked, vector_weight), (bm25_ranked, bm25_weight)):
        for position, doc in enumerate(ranked, start=1):
            scores[doc] += weight / (k + position)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


async def sweep_fusion(embedder_kind: str, report_path: str | None) -> None:
    """Grid-search RRF k and vector:bm25 weighting over one retrieval pass."""
    from eval_db import fresh_database

    from unstash.documents.embedder import EmbeddingTask

    golden = [q for q in load_golden() if q["category"] != "no_answer"]
    embedder = build_embedder(embedder_kind)
    collected = []

    async with fresh_database() as pool:
        await ingest_corpus(pool, embedder)
        for q in golden:
            qvec_batch = await embedder.embed([q["query"]], task=EmbeddingTask.QUERY)
            qvec = "[" + ",".join(str(v) for v in qvec_batch.vectors[0]) + "]"
            collected.append((q, await rank_vector(pool, qvec), await rank_bm25(pool, q["query"])))

    def row(name, ranker):
        overall, mrr_all = [], []
        cats = defaultdict(list)
        for q, vec, bm in collected:
            judgments = {rel["doc"]: rel["grade"] for rel in q["relevant"]}
            ranked = ranker(vec, bm)
            overall.append(ndcg_at_k(ranked, judgments, 10))
            mrr_all.append(mrr(ranked, judgments))
            cats[q["category"]].append(ndcg_at_k(ranked, judgments, 10))

        def avg(v):
            return f"{sum(v) / len(v):.3f}" if v else "-"

        return (
            f"| {name} | {avg(overall)} | {avg(mrr_all)} | {avg(cats['keyword'])} |"
            f" {avg(cats['semantic'])} | {avg(cats['decision'])} | {avg(cats['english'])} |"
        )

    lines = [f"# Fusion sweep — embedder={embedder_kind}, {len(golden)} queries", ""]
    lines.append("| config | all nDCG@10 | all MRR | keyword | semantic | decision | english |")
    lines.append("|---|---|---|---|---|---|---|")
    lines.append(row("vector only", lambda vec, bm: vec))
    lines.append(row("bm25 only", lambda vec, bm: bm))
    for k in (20, 60, 120):
        for wv in (1.0, 2.0, 3.0, 4.0):
            lines.append(
                row(
                    f"rrf k={k} w={wv:g}:1",
                    lambda vec, bm, k=k, wv=wv: rank_rrf(vec, bm, k, wv, 1.0),
                )
            )
    report = "\n".join(lines)
    print("\n" + report)
    if report_path:
        out = HERE / report_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report + "\n")
        print(f"\nwritten to {out}")


SWEDISH_BM25_INDEX = (
    "CREATE INDEX ix_chunks_text_bm25 ON chunks "
    "USING bm25 (id, (text::pdb.icu('stemmer=swedish')), org_id) "
    "WITH (key_field='id')"
)


def _primary_doc(query: dict) -> str:
    """Cluster key: the query's highest-graded relevant document.

    Queries answering to the same document (e.g. the roof cluster) share a
    key, so the bootstrap treats them as one dependent group rather than as
    independent observations. Ties break on ``(-grade, doc)`` so the key is
    stable regardless of the order documents appear in ``relevant``.
    """
    return min(query["relevant"], key=lambda rel: (-rel["grade"], rel["doc"]))["doc"]


def _stats_lines(
    per_config: dict[str, dict[str, list[float]]],
    categories: list[str],
    clusters: list[str],
    no_answer_count: int,
) -> list[str]:
    """Paired comparison and per-slice CIs, resampled by source document."""
    n_clusters_all = len(set(clusters))

    def _withheld_reason(n_clusters: int) -> str:
        if n_clusters < MIN_CLUSTERS_FOR_CI:
            return f"interval withheld — {n_clusters} source documents"
        return "interval withheld — identical scores across documents"

    def paired(a: str, b: str, metric: str) -> str:
        mean, low, high, pvalue = clustered_paired_test(
            per_config[a][metric], per_config[b][metric], clusters
        )
        ci = (
            _withheld_reason(n_clusters_all)
            if math.isnan(low)
            else f"95% CI [{low:+.3f}, {high:+.3f}]"
        )
        return f"{mean:+.3f}  {ci}  permutation p={pvalue:.3f}"

    lines = [
        "",
        f"## Paired comparison — all/ndcg@10 ({n_clusters_all} source documents)",
        "",
        f"- rrf vs vector: {paired('rrf', 'vector', 'all/ndcg@10')}",
        f"- rrf vs bm25:   {paired('rrf', 'bm25', 'all/ndcg@10')}",
        "",
        "## Per-slice ndcg@10, rrf — mean with 95% CI (clustered)",
        "",
    ]
    for cat in sorted(set(categories)):
        idx = [i for i, category in enumerate(categories) if category == cat]
        vals = [per_config["rrf"]["all/ndcg@10"][i] for i in idx]
        slice_clusters = [clusters[i] for i in idx]
        mean, low, high = clustered_bootstrap_ci(vals, slice_clusters)
        ci = (
            _withheld_reason(len(set(slice_clusters)))
            if math.isnan(low)
            else f"[{low:.3f}, {high:.3f}]"
        )
        lines.append(
            f"- {cat} (n={len(vals)}, {len(set(slice_clusters))} clusters): {mean:.3f} {ci}"
        )
    lines += [
        "",
        f"_{no_answer_count} no_answer queries are authored but excluded from ranking "
        "metrics (undefined on an empty judgment set). The refusal path is a "
        "retrieval-correctness property tracked separately — not reflected above._",
    ]
    return lines


async def run(
    embedder_kind: str,
    report_path: str | None,
    bm25_tokenizer: str = "icu",
    json_path: str | None = None,
) -> None:
    from eval_db import fresh_database

    from unstash.documents.embedder import EmbeddingTask

    all_golden = load_golden()
    golden = [q for q in all_golden if q["category"] != "no_answer"]
    no_answer = [q for q in all_golden if q["category"] == "no_answer"]
    embedder = build_embedder(embedder_kind)

    async with fresh_database() as pool:
        if bm25_tokenizer == "swedish":
            await pool.execute("DROP INDEX ix_chunks_text_bm25")
            await pool.execute(SWEDISH_BM25_INDEX)
            print("bm25 index recreated with stemmer=swedish", flush=True)
        await ingest_corpus(pool, embedder)

        per_config: dict[str, dict[str, list[float]]] = {
            name: defaultdict(list) for name in ("vector", "bm25", "rrf")
        }
        # Per-query cluster keys (source document), aligned with the score
        # lists, so the bootstrap resamples documents rather than queries.
        categories: list[str] = []
        clusters: list[str] = []
        for q in golden:
            judgments = {rel["doc"]: rel["grade"] for rel in q["relevant"]}
            categories.append(q["category"])
            clusters.append(_primary_doc(q))
            qvec_batch = await embedder.embed([q["query"]], task=EmbeddingTask.QUERY)
            qvec = "[" + ",".join(str(v) for v in qvec_batch.vectors[0]) + "]"

            vec = await rank_vector(pool, qvec)
            bm = await rank_bm25(pool, q["query"])
            configs = {"vector": vec, "bm25": bm, "rrf": rank_rrf(vec, bm)}

            for name, ranked in configs.items():
                bucket = per_config[name]
                bucket["all/recall@10"].append(recall_at_k(ranked, judgments, 10))
                bucket["all/mrr"].append(mrr(ranked, judgments))
                bucket["all/ndcg@10"].append(ndcg_at_k(ranked, judgments, 10))
                cat = q["category"]
                bucket[f"{cat}/ndcg@10"].append(ndcg_at_k(ranked, judgments, 10))

    lines = [
        f"# Retrieval eval — embedder={embedder_kind}, bm25={bm25_tokenizer}, {len(golden)} queries",
        "",
    ]
    header = sorted({key for c in per_config.values() for key in c})
    lines.append("| metric | " + " | ".join(per_config) + " |")
    lines.append("|---|" + "---|" * len(per_config))
    for key in header:
        row = [
            f"{sum(per_config[c][key]) / len(per_config[c][key]):.3f}"
            if per_config[c][key]
            else "-"
            for c in per_config
        ]
        lines.append(f"| {key} | " + " | ".join(row) + " |")
    lines += _stats_lines(per_config, categories, clusters, len(no_answer))

    report = "\n".join(lines)
    print("\n" + report)
    if report_path:
        out = HERE / report_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report + "\n")
        print(f"\nwritten to {out}")
    if json_path:
        aggregated = {
            config: {metric: sum(vals) / len(vals) for metric, vals in metrics.items() if vals}
            for config, metrics in per_config.items()
        }
        out = HERE / json_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(aggregated, indent=2, sort_keys=True) + "\n")
        print(f"metrics json written to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", choices=["jina", "fake"], default="jina")
    parser.add_argument("--report", default=None)
    parser.add_argument("--bm25", choices=["icu", "swedish"], default="icu")
    parser.add_argument("--sweep-fusion", action="store_true")
    parser.add_argument("--json", default=None, help="write aggregated metrics as JSON")
    args = parser.parse_args()
    if args.sweep_fusion:
        asyncio.run(sweep_fusion(args.embedder, args.report))
    else:
        asyncio.run(run(args.embedder, args.report, args.bm25, args.json))
