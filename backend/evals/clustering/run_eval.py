"""Clustering quality against the synthetic corpus's ground-truth types.

Each corpus document declares its type in a header comment
(``<!-- synthetic eval document | type: styrelseprotokoll | ... -->``).
This runner chunks and embeds the corpus exactly as production does
(``parse_to_chunks`` + mean-pooled chunk embeddings), runs the clustering
engine, and scores the assignments against the declared types: ARI, NMI,
purity, noise fraction, plus a per-cluster composition table.

No database — the engine is pure, so the harness is file → embed → cluster.

Usage::

    python run_eval.py --embedder fake     # plumbing smoke; quality numbers meaningless
    JINA_API_KEY=... python run_eval.py    # the measurement (real embeddings)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND / "src"))

import numpy as np

_TYPE_RE = re.compile(r"<!--\s*synthetic eval document\s*\|\s*type:\s*([\w-]+)")
_LEAD_TEXT_CHARS = 2000  # mirrors clustering.service


def build_embedder(kind: str):
    from unstash.documents.embedder import FakeEmbedder

    if kind == "fake":
        return FakeEmbedder(dimensions=2048)
    from unstash.inference.jina import JinaEmbedder

    api_key = os.environ.get("JINA_API_KEY", "")
    if not api_key:
        sys.exit("JINA_API_KEY is required for --embedder jina")
    return JinaEmbedder(
        api_key=api_key,
        base_url="https://api.jina.ai/v1",
        model="jina-embeddings-v4",
        dimensions=2048,
        timeout=60.0,
    )


async def load_corpus(embedder) -> tuple[list[str], list[str], np.ndarray, list[str]]:
    """Return (names, texts, pooled embeddings, ground-truth types)."""
    from unstash.documents.embedder import EmbeddingTask
    from unstash.documents.parser import parse_to_chunks

    corpus = BACKEND / "evals" / "retrieval" / "corpus"
    names: list[str] = []
    texts: list[str] = []
    pooled: list[np.ndarray] = []
    truth: list[str] = []
    for path in sorted(corpus.glob("*.md")):
        match = _TYPE_RE.search(path.read_text(encoding="utf-8")[:200])
        if match is None:
            print(f"skipping {path.name}: no type header", flush=True)
            continue
        parsed = parse_to_chunks(path)
        chunk_texts = [c.text for c in parsed.chunks]
        batch = await embedder.embed(chunk_texts, task=EmbeddingTask.PASSAGE)
        vectors = np.asarray(batch.vectors, dtype=np.float64)
        names.append(path.name)
        texts.append(f"{path.name}\n{chunk_texts[0][:_LEAD_TEXT_CHARS]}")
        pooled.append(vectors.mean(axis=0))
        truth.append(match.group(1))
    return names, texts, np.stack(pooled), truth


def purity(assignments: list[int], truth: list[str]) -> float:
    """Fraction of non-noise documents whose cluster's majority type is theirs."""
    clusters: dict[int, Counter] = {}
    for cluster_id, doc_type in zip(assignments, truth, strict=True):
        if cluster_id != -1:
            clusters.setdefault(cluster_id, Counter())[doc_type] += 1
    clustered = sum(sum(c.values()) for c in clusters.values())
    if clustered == 0:
        return 0.0
    majority = sum(c.most_common(1)[0][1] for c in clusters.values())
    return majority / clustered


async def load_corpus_cached(embedder, embedder_kind: str):
    """Cache the pooled embeddings per embedder kind; corpus text is cheap."""
    cache = HERE / f"embeddings-{embedder_kind}.npz"
    if cache.exists():
        stored = np.load(cache, allow_pickle=True)
        print(f"using cached embeddings from {cache.name}", flush=True)
        return (
            list(stored["names"]),
            list(stored["texts"]),
            stored["embeddings"],
            list(stored["truth"]),
        )
    names, texts, embeddings, truth = await load_corpus(embedder)
    np.savez(cache, names=names, texts=texts, embeddings=embeddings, truth=truth)
    return names, texts, embeddings, truth


async def run(embedder_kind: str, json_path: str | None, selection: str) -> None:
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    from unstash.clustering.engine import cluster_documents, keyword_label

    embedder = build_embedder(embedder_kind)
    names, texts, embeddings, truth = await load_corpus_cached(embedder, embedder_kind)
    print(f"corpus: {len(names)} documents, {len(set(truth))} ground-truth types", flush=True)

    result = cluster_documents(texts, embeddings, cluster_selection_method=selection)
    noise = sum(1 for a in result.assignments if a == -1)

    non_noise = [(a, t) for a, t in zip(result.assignments, truth, strict=True) if a != -1]
    ari = adjusted_rand_score([t for _, t in non_noise], [a for a, _ in non_noise])
    nmi = normalized_mutual_info_score([t for _, t in non_noise], [a for a, _ in non_noise])
    pur = purity(result.assignments, truth)

    print(f"\nclusters: {len(result.keywords)}  noise: {noise}/{len(names)}")
    print(f"ARI: {ari:.3f}  NMI: {nmi:.3f}  purity: {pur:.3f}")
    print(f"silhouette (umap space): {result.silhouette}")

    print("\nper-cluster composition:")
    for cluster_id, terms in sorted(result.keywords.items()):
        members = [
            (name, doc_type)
            for name, doc_type, assigned in zip(names, truth, result.assignments, strict=True)
            if assigned == cluster_id
        ]
        types = Counter(doc_type for _, doc_type in members)
        composition = ", ".join(f"{t}x{c}" for t, c in types.most_common())
        print(f"  [{cluster_id}] {len(members):>2} docs  «{keyword_label(terms)}»  {composition}")

    if json_path:
        payload = (
            json.dumps(
                {
                    "embedder": embedder_kind,
                    "documents": len(names),
                    "types": len(set(truth)),
                    "clusters": len(result.keywords),
                    "noise": noise,
                    "ari": round(ari, 4),
                    "nmi": round(nmi, 4),
                    "purity": round(pur, 4),
                    "silhouette": result.silhouette,
                    "params": result.params,
                },
                indent=2,
            )
            + "\n"
        )
        await asyncio.to_thread(Path(json_path).write_text, payload, encoding="utf-8")
        print(f"\nwrote {json_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", choices=["jina", "fake"], default="jina")
    parser.add_argument("--selection", choices=["eom", "leaf"], default="eom")
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()
    asyncio.run(run(args.embedder, args.json_path, args.selection))
