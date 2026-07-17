"""Embedding-model bake-off (#71): Jina v4 vs Jina v5-text-small vs BGE-M3.

One text-only ingest supplies the shared BM25 lane (stemmed index at
migration head). Each model embeds the same chunks and queries; vector
ranking is computed in-memory (exact cosine, doc-level best chunk) so
column width never touches the schema, and fusion uses the adopted
weighted RRF (k=20, vector:bm25 3:1).

BGE-M3 runs locally in dense mode only (its sparse/ColBERT outputs are a
separate architectural question, noted in the M4 plan). Run with:

    uv run --with sentence-transformers python evals/retrieval/bake_off.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import asyncpg  # noqa: E402
import numpy as np  # noqa: E402
from eval_db import fresh_database  # noqa: E402
from metrics import mrr, ndcg_at_k, recall_at_k  # noqa: E402
from run_eval import load_golden, rank_bm25, rank_rrf  # noqa: E402

RRF_K = 20
VECTOR_WEIGHT = 3.0


async def ingest_text_only(pool: asyncpg.Pool) -> tuple[list[str], list[str], list[int]]:
    """Insert documents + chunks (no embeddings); return texts and doc mapping."""
    from unstash.documents.parser import parse_to_chunks

    org_id = await pool.fetchval(
        "INSERT INTO organisations (slug, name) VALUES ('eval', 'Eval Org') RETURNING id"
    )
    chunk_texts: list[str] = []
    chunk_doc: list[int] = []
    doc_titles: list[str] = []
    for path in sorted((HERE / "corpus").glob("*.md")):
        parsed = parse_to_chunks(path)
        doc_id = await pool.fetchval(
            "INSERT INTO documents (org_id, title, source_uri, mime_type, size_bytes,"
            " content_hash, status) VALUES ($1, $2, $3, 'text/markdown', $4, $5, 'indexed')"
            " RETURNING id",
            org_id, path.name, str(path), path.stat().st_size, path.name,
        )
        doc_index = len(doc_titles)
        doc_titles.append(path.name)
        for chunk in parsed.chunks:
            await pool.execute(
                "INSERT INTO chunks (org_id, document_id, chunk_index, text, token_count,"
                " char_offset_start, char_offset_end) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                org_id, doc_id, chunk.chunk_index, chunk.text, chunk.token_count,
                chunk.char_offset_start, chunk.char_offset_end,
            )
            chunk_texts.append(chunk.text)
            chunk_doc.append(doc_index)
    print(f"ingested {len(doc_titles)} docs, {len(chunk_texts)} chunks (text only)", flush=True)
    return doc_titles, chunk_texts, chunk_doc


def rank_vector_memory(
    query_vec: np.ndarray,
    chunk_matrix: np.ndarray,
    chunk_doc: list[int],
    doc_titles: list[str],
) -> list[str]:
    """Doc-level ranking by best-chunk cosine (vectors pre-normalised)."""
    sims = chunk_matrix @ query_vec
    best: dict[int, float] = {}
    for idx, sim in enumerate(sims):
        d = chunk_doc[idx]
        if sim > best.get(d, -2.0):
            best[d] = float(sim)
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    return [doc_titles[d] for d, _ in ranked]


def normalise(matrix: np.ndarray) -> np.ndarray:
    return matrix / np.linalg.norm(matrix, axis=-1, keepdims=True)


async def embed_jina(model: str, dims: int, texts: list[str], task: str) -> np.ndarray:
    from unstash.documents.embedder import EmbeddingTask, JinaEmbedder

    embedder = JinaEmbedder(
        api_key=os.environ["JINA_API_KEY"],
        base_url="https://api.jina.ai/v1",
        model=model,
        dimensions=dims,
        timeout=90.0,
    )
    task_enum = EmbeddingTask.PASSAGE if task == "passage" else EmbeddingTask.QUERY
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 32):
        batch = await embedder.embed(texts[start : start + 32], task=task_enum)
        vectors.extend(batch.vectors)
    return normalise(np.array(vectors, dtype=np.float32))


def embed_bge_m3(texts: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("BAAI/bge-m3")
    vectors = model.encode(texts, batch_size=16, show_progress_bar=False)
    return normalise(np.array(vectors, dtype=np.float32))


async def main() -> None:
    golden = [q for q in load_golden() if q["category"] != "no_answer"]
    queries = [q["query"] for q in golden]

    async with fresh_database() as pool:
        doc_titles, chunk_texts, chunk_doc = await ingest_text_only(pool)
        bm25_ranked = [await rank_bm25(pool, q) for q in queries]

    models: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    print("embedding: jina v4 ...", flush=True)
    models["jina-v4 (2048)"] = (
        await embed_jina("jina-embeddings-v4", 2048, chunk_texts, "passage"),
        await embed_jina("jina-embeddings-v4", 2048, queries, "query"),
    )
    print("embedding: jina v5-text-small ...", flush=True)
    models["jina-v5-small (1024)"] = (
        await embed_jina("jina-embeddings-v5-text-small", 1024, chunk_texts, "passage"),
        await embed_jina("jina-embeddings-v5-text-small", 1024, queries, "query"),
    )
    print("embedding: bge-m3 (local, dense) ...", flush=True)
    models["bge-m3 (1024)"] = (
        embed_bge_m3(chunk_texts),
        embed_bge_m3(queries),
    )

    lines = [f"# Embedding bake-off — {len(golden)} queries, shared stemmed BM25", ""]
    lines.append(
        "| model / config | all nDCG@10 | all MRR | all recall@10 |"
        " keyword | semantic | decision | english |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")

    for name, (chunk_matrix, query_matrix) in models.items():
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
                f"| {name} {config} | {avg(overall)} | {avg(mrrs)} | {avg(recalls)} |"
                f" {avg(cats['keyword'])} | {avg(cats['semantic'])} |"
                f" {avg(cats['decision'])} | {avg(cats['english'])} |"
            )

    report = "\n".join(lines)
    print("\n" + report)
    out = HERE / "reports" / "experiment-71-embedding-bakeoff.md"
    out.write_text(report + "\n")
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    asyncio.run(main())
