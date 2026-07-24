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
    MISTRAL_API_KEY=... ... --ocr   # OCR scanned PDFs via the production fallback
    ... --sample-seed 7             # seeded random sample instead of path order
    MISTRAL_API_KEY=... ... --label # LLM labels via the production labeler

OCR results are cached under ~/.cache/unstash-local-eval/ocr keyed on file
content, so re-runs do not re-pay the OCR call. The cache lives outside the
corpus directory — nothing is ever written into the scanned tree.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND / "src"))

import numpy as np
from run_eval import _LEAD_TEXT_CHARS, build_embedder

SUFFIXES = {".pdf", ".docx", ".md", ".txt"}
# Mirrors the production ingestion defaults for the OCR fallback.
OCR_MIN_CHARS_PER_PAGE = 200
OCR_MAX_BYTES = 50 * 1024 * 1024
OCR_CACHE = Path.home() / ".cache" / "unstash-local-eval" / "ocr"
# Pooled document embeddings, keyed on file content like the OCR cache, so
# parameter iteration re-clusters without re-parsing or re-embedding.
EMB_CACHE = Path.home() / ".cache" / "unstash-local-eval" / "emb"


def _find_files(corpus_dir: Path, max_files: int, sample_seed: int | None) -> list[Path]:
    """All supported files, either path-ordered or a seeded random sample.

    Path order concentrates on one subtree; a seeded sample spans the whole
    archive, which is the representative test.
    """
    all_files = sorted(p for p in corpus_dir.rglob("*") if p.suffix.lower() in SUFFIXES)
    if sample_seed is None or len(all_files) <= max_files:
        return all_files[:max_files]
    import random

    rng = random.Random(sample_seed)
    return sorted(rng.sample(all_files, max_files))


async def _chunk_texts_with_ocr(path: Path, use_ocr: bool) -> list[str] | None:
    """Chunk texts for one file; OCR fallback for scanned PDFs when enabled."""
    from unstash.documents.ocr import needs_ocr, ocr_pdf_to_markdown
    from unstash.documents.parser import parse_to_chunks

    parsed = await asyncio.to_thread(parse_to_chunks, path)
    chunk_texts = [c.text for c in parsed.chunks]
    scanned = path.suffix.lower() == ".pdf" and needs_ocr(
        page_count=parsed.page_count,
        total_chars=parsed.total_chars,
        min_chars_per_page=OCR_MIN_CHARS_PER_PAGE,
    )
    if not scanned:
        return chunk_texts or None
    if not use_ocr:
        print(f"  skipping {path.name}: scanned, OCR not enabled", flush=True)
        return None

    digest = hashlib.sha256(await asyncio.to_thread(path.read_bytes)).hexdigest()
    cached = OCR_CACHE / f"{digest}.md"
    if not cached.exists():
        markdown = await ocr_pdf_to_markdown(
            path,
            api_key=os.environ["MISTRAL_API_KEY"],
            base_url="https://api.mistral.ai",
            model="mistral-ocr-latest",
            max_bytes=OCR_MAX_BYTES,
            timeout=120.0,
        )
        await asyncio.to_thread(cached.write_text, markdown, "utf-8")
    ocr_parsed = await asyncio.to_thread(parse_to_chunks, cached)
    return [c.text for c in ocr_parsed.chunks] or None


async def _llm_labels(result, names: list[str], texts: list[str]) -> dict[int, str]:
    """One production-labeler call per cluster; failures keep keyword labels."""
    from unstash.clustering.labeler import LabelError, LabelRequest
    from unstash.inference.litellm_chat import LiteLlmLabeler

    class _Settings:
        labeler_model = "mistral/mistral-small-2603"
        mistral_api_key = os.environ["MISTRAL_API_KEY"]
        labeler_timeout_seconds = 30.0

    labeler = LiteLlmLabeler(_Settings())  # type: ignore[arg-type]
    labels: dict[int, str] = {}
    for cluster_id, terms in sorted(result.keywords.items()):
        reps = result.representatives[cluster_id]
        request = LabelRequest(
            keywords=[t for t, _ in terms],
            representative_titles=[texts[i].split("\n", 1)[0] for i in reps],
            representative_excerpts=[texts[i].split("\n", 1)[-1][:200] for i in reps],
            language="sv",
        )
        try:
            labels[cluster_id] = (await labeler.label(request)).label
        except LabelError as exc:
            print(f"  labeling cluster {cluster_id} failed: {exc}", flush=True)
    return labels


async def _load_document(path: Path, use_ocr: bool, embedder):
    """(text, pooled embedding) for one file, via the content-keyed cache."""
    from unstash.documents.embedder import EmbeddingTask
    from unstash.documents.ocr import OcrError

    digest = hashlib.sha256(await asyncio.to_thread(path.read_bytes)).hexdigest()
    cached = EMB_CACHE / f"{digest}.npz"
    if cached.exists():
        stored = np.load(cached, allow_pickle=True)
        return str(stored["text"]), stored["embedding"]
    try:
        chunk_texts = await _chunk_texts_with_ocr(path, use_ocr)
    except OcrError as exc:
        print(f"  skipping {path.name}: OCR failed ({exc})", flush=True)
        return None
    except Exception as exc:
        print(f"  skipping {path.name}: {type(exc).__name__}", flush=True)
        return None
    if not chunk_texts:
        print(f"  skipping {path.name}: no text", flush=True)
        return None
    batch = await embedder.embed(chunk_texts, task=EmbeddingTask.PASSAGE)
    text = f"{path.name}\n{chunk_texts[0][:_LEAD_TEXT_CHARS]}"
    embedding = np.asarray(batch.vectors, dtype=np.float64).mean(axis=0)
    np.savez(cached, text=text, embedding=embedding)
    return text, embedding


async def main(
    corpus_dir: Path,
    selection: str,
    max_files: int,
    use_ocr: bool,
    sample_seed: int | None,
    use_labels: bool,
) -> None:
    from unstash.clustering.engine import cluster_documents, keyword_label

    if (use_ocr or use_labels) and not os.environ.get("MISTRAL_API_KEY"):
        sys.exit("MISTRAL_API_KEY is required with --ocr / --label")
    OCR_CACHE.mkdir(parents=True, exist_ok=True)
    EMB_CACHE.mkdir(parents=True, exist_ok=True)

    files = await asyncio.to_thread(_find_files, corpus_dir, max_files, sample_seed)
    if not files:
        sys.exit(f"no supported documents under {corpus_dir}")
    print(f"parsing and embedding {len(files)} documents ...", flush=True)

    embedder = build_embedder("jina")
    names: list[str] = []
    texts: list[str] = []
    pooled: list[np.ndarray] = []
    for path in files:
        loaded = await _load_document(path, use_ocr, embedder)
        if loaded is None:
            continue
        text, embedding = loaded
        names.append(path.name)
        texts.append(text)
        pooled.append(embedding)

    print(f"clustering {len(names)} documents (selection={selection}) ...", flush=True)
    result = cluster_documents(texts, np.stack(pooled), cluster_selection_method=selection)

    llm_labels = await _llm_labels(result, names, texts) if use_labels else {}

    noise = [names[i] for i, a in enumerate(result.assignments) if a == -1]
    print(f"\nclusters: {len(result.keywords)}  noise: {len(noise)}/{len(names)}")
    print(f"silhouette (umap space): {result.silhouette}")
    for cluster_id, terms in sorted(result.keywords.items()):
        members = [names[i] for i, a in enumerate(result.assignments) if a == cluster_id]
        shown = llm_labels.get(cluster_id, keyword_label(terms))
        print(f"\n[{cluster_id}] {len(members)} docs  «{shown}»")
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
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--sample-seed", type=int, default=None)
    parser.add_argument("--label", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        main(
            args.corpus_dir, args.selection, args.max_files, args.ocr, args.sample_seed, args.label
        )
    )
