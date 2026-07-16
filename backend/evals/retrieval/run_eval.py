"""Retrieval evaluation runner.

Ingests the synthetic corpus into a fresh Postgres (testcontainer, schema
at migration head), embeds it, runs each retrieval configuration over the
golden queries, and reports Recall@10, MRR, and nDCG@10 per category.

Configurations: vector-only, bm25-only, rrf (fusion in Python here; the
production search endpoint implements the same math in SQL). Reranking is
added as a fourth configuration when the rerank client lands (Phase C).

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
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import asyncpg  # noqa: E402
from metrics import mrr, ndcg_at_k, recall_at_k  # noqa: E402

RRF_K = 60
TOP_N = 50


def load_golden() -> list[dict]:
    lines = (HERE / "golden.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def build_embedder(kind: str):
    from unstash.documents.embedder import FakeEmbedder, JinaEmbedder

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


def rank_rrf(vector_ranked: list[str], bm25_ranked: list[str]) -> list[str]:
    scores: dict[str, float] = defaultdict(float)
    for ranked in (vector_ranked, bm25_ranked):
        for position, doc in enumerate(ranked, start=1):
            scores[doc] += 1.0 / (RRF_K + position)
    return [doc for doc, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


SWEDISH_BM25_INDEX = (
    "CREATE INDEX ix_chunks_text_bm25 ON chunks "
    "USING bm25 (id, (text::pdb.icu('stemmer=swedish')), org_id) "
    "WITH (key_field='id')"
)


async def run(embedder_kind: str, report_path: str | None, bm25_tokenizer: str = "icu") -> None:
    from eval_db import fresh_database  # noqa: PLC0415

    from unstash.documents.embedder import EmbeddingTask

    golden = [q for q in load_golden() if q["category"] != "no_answer"]
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
        for q in golden:
            judgments = {rel["doc"]: rel["grade"] for rel in q["relevant"]}
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
    report = "\n".join(lines)
    print("\n" + report)
    if report_path:
        out = HERE / report_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report + "\n")
        print(f"\nwritten to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", choices=["jina", "fake"], default="jina")
    parser.add_argument("--report", default=None)
    parser.add_argument("--bm25", choices=["icu", "swedish"], default="icu")
    args = parser.parse_args()
    asyncio.run(run(args.embedder, args.report, args.bm25))
