"""Retrieval latency at scale (M4 Phase C / #99).

Seeds ~10k chunks with deterministic fake embeddings into a fresh
Postgres (DiskANN + BM25 indexes at migration head) and times the
database-side retrieval path — vector top-50 + BM25 top-50 + weighted
RRF fusion — with no network in the loop.

This isolates the component that grows with corpus size. The per-query
inference cost (query embedding + rerank) is a constant two external
round-trips, measured separately against staging; total p95 is this
plus that constant. Run with:

    python evals/retrieval/latency_check.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import asyncpg
from eval_db import fresh_database
from run_eval import rank_bm25, rank_rrf, rank_vector

from unstash.documents.embedder import EmbeddingTask, FakeEmbedder

TARGET_CHUNKS = 10_000
CHUNKS_PER_DOC = 20
EMBED_DIM = 2048
RRF_K = 20
VECTOR_WEIGHT = 3.0
ITERATIONS = 200

_QUERY_TERMS = [
    "styrelsen beslutade om takrenovering",
    "årsredovisning och budget för föreningen",
    "avtal om elleverans och fjärrvärme",
    "stämma och val av styrelse",
    "underhållsplan för fastigheten",
]
_WORDS = [
    "styrelsen",
    "beslutade",
    "att",
    "renovera",
    "taket",
    "avgift",
    "ekonomi",
    "budget",
    "stämma",
    "underhåll",
    "fastighet",
    "lägenhet",
    "avtal",
    "leverantör",
    "offert",
    "protokoll",
    "möte",
    "förening",
    "medlem",
    "ordförande",
    "kassör",
    "revisor",
    "balansräkning",
    "fjärrvärme",
]


def _chunk_text(doc: int, idx: int) -> str:
    span = _WORDS[(doc + idx) % len(_WORDS) :] + _WORDS[: (doc + idx) % len(_WORDS)]
    return f"Dokument {doc} stycke {idx}: " + " ".join(span)


async def _seed(pool: asyncpg.Pool) -> None:
    embedder = FakeEmbedder(dimensions=EMBED_DIM)
    org_id = await pool.fetchval(
        "INSERT INTO organisations (slug, name) VALUES ('perf', 'Perf Org') RETURNING id",
    )
    docs = TARGET_CHUNKS // CHUNKS_PER_DOC
    print(
        f"seeding {docs} docs x {CHUNKS_PER_DOC} = {docs * CHUNKS_PER_DOC} chunks ...", flush=True
    )
    for doc in range(docs):
        document_id = await pool.fetchval(
            "INSERT INTO documents (org_id, title, source_uri, mime_type, size_bytes,"
            " content_hash, status) VALUES ($1, $2, $3, 'text/markdown', 100, $4, 'indexed')"
            " RETURNING id",
            org_id,
            f"doc-{doc}.md",
            f"/perf/doc-{doc}.md",
            f"hash-{doc}",
        )
        texts = [_chunk_text(doc, i) for i in range(CHUNKS_PER_DOC)]
        batch = await embedder.embed(texts, task=EmbeddingTask.PASSAGE)
        await pool.executemany(
            "INSERT INTO chunks (org_id, document_id, chunk_index, text, token_count,"
            " char_offset_start, char_offset_end, embedding)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)",
            [
                (
                    org_id,
                    document_id,
                    i,
                    texts[i],
                    len(texts[i]) // 4,
                    0,
                    len(texts[i]),
                    "[" + ",".join(str(v) for v in vector) + "]",
                )
                for i, vector in enumerate(batch.vectors)
            ],
        )
    total = await pool.fetchval("SELECT count(*) FROM chunks")
    print(f"seeded; {total} chunks indexed", flush=True)


def _pct(values: list[float], p: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * p))]


async def main() -> None:
    embedder = FakeEmbedder(dimensions=EMBED_DIM)
    async with fresh_database() as pool:
        await _seed(pool)

        query_vecs = []
        for term in _QUERY_TERMS:
            batch = await embedder.embed([term], task=EmbeddingTask.QUERY)
            query_vecs.append("[" + ",".join(str(v) for v in batch.vectors[0]) + "]")

        # Warm the plan/caches.
        await rank_vector(pool, query_vecs[0])
        await rank_bm25(pool, _QUERY_TERMS[0])

        timings: list[float] = []
        for i in range(ITERATIONS):
            term = _QUERY_TERMS[i % len(_QUERY_TERMS)]
            qvec = query_vecs[i % len(query_vecs)]
            start = time.perf_counter()
            vec = await rank_vector(pool, qvec)
            bm = await rank_bm25(pool, term)
            rank_rrf(vec, bm, RRF_K, VECTOR_WEIGHT, 1.0)
            timings.append((time.perf_counter() - start) * 1000)

    print(f"\nDB retrieval latency over {ITERATIONS} queries at {TARGET_CHUNKS} chunks:")
    print(
        f"  p50={_pct(timings, 0.5):.1f}ms  p95={_pct(timings, 0.95):.1f}ms  "
        f"p99={_pct(timings, 0.99):.1f}ms  max={max(timings):.1f}ms  min={min(timings):.1f}ms"
    )


if __name__ == "__main__":
    asyncio.run(main())
