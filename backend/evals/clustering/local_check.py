"""Qualitative clustering check over a local document directory.

Runs the production clustering pipeline (parse → embed → mean-pool →
cluster) over an operator-provided directory of documents and prints the
discovered structure: cluster sizes, keywords, and member filenames. For
judging whether discovered categories match an operator's intuition about
a real corpus — nothing is written anywhere; output is the terminal.

Supported inputs: whatever the production parser supports (PDF, DOCX,
Markdown, plain text). Files that fail to parse are skipped with a notice.

    JINA_API_KEY=... python evals/clustering/local_check.py --corpus-dir /path/to/documents
    ... --selection leaf        # compare extraction granularity
    ... --max-files 200         # bound a large directory
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND / "src"))

import numpy as np
from run_eval import _LEAD_TEXT_CHARS, build_embedder

SUFFIXES = {".pdf", ".docx", ".md", ".txt"}


async def main(corpus_dir: Path, selection: str, max_files: int) -> None:
    from unstash.clustering.engine import cluster_documents, keyword_label
    from unstash.documents.embedder import EmbeddingTask
    from unstash.documents.parser import parse_to_chunks

    files = sorted(p for p in corpus_dir.rglob("*") if p.suffix.lower() in SUFFIXES)[:max_files]
    if not files:
        sys.exit(f"no supported documents under {corpus_dir}")
    print(f"parsing and embedding {len(files)} documents ...", flush=True)

    embedder = build_embedder("jina")
    names: list[str] = []
    texts: list[str] = []
    pooled: list[np.ndarray] = []
    for path in files:
        try:
            parsed = parse_to_chunks(path)
        except Exception as exc:
            print(f"  skipping {path.name}: {type(exc).__name__}", flush=True)
            continue
        chunk_texts = [c.text for c in parsed.chunks]
        if not chunk_texts:
            print(f"  skipping {path.name}: no text", flush=True)
            continue
        batch = await embedder.embed(chunk_texts, task=EmbeddingTask.PASSAGE)
        names.append(path.name)
        texts.append(f"{path.name}\n{chunk_texts[0][:_LEAD_TEXT_CHARS]}")
        pooled.append(np.asarray(batch.vectors, dtype=np.float64).mean(axis=0))

    print(f"clustering {len(names)} documents (selection={selection}) ...", flush=True)
    result = cluster_documents(texts, np.stack(pooled), cluster_selection_method=selection)

    noise = [names[i] for i, a in enumerate(result.assignments) if a == -1]
    print(f"\nclusters: {len(result.keywords)}  noise: {len(noise)}/{len(names)}")
    print(f"silhouette (umap space): {result.silhouette}")
    for cluster_id, terms in sorted(result.keywords.items()):
        members = [names[i] for i, a in enumerate(result.assignments) if a == cluster_id]
        print(f"\n[{cluster_id}] {len(members)} docs  «{keyword_label(terms)}»")
        for name in sorted(members):
            print(f"    {name}")
    if noise:
        print(f"\n[noise] {len(noise)} docs")
        for name in sorted(noise):
            print(f"    {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--selection", choices=["eom", "leaf"], default="eom")
    parser.add_argument("--max-files", type=int, default=500)
    args = parser.parse_args()
    asyncio.run(main(args.corpus_dir, args.selection, args.max_files))
