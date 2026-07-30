"""Retrieval traces over a local document directory, for hand-read error analysis.

Indexes an operator-provided directory into a fresh Postgres at migration
head using the production parser, OCR fallback and embedder, then runs a
list of real queries through the production search pipeline
(:func:`unstash.search.service.run_search`) and writes one trace per query
for manual coding.

This is the instrument for error analysis: the step that produces a failure
taxonomy from observed traces rather than a metric from a golden set. It
deliberately reports **no scores**. There are no relevance judgments for
these queries, and a number computed without them would be invented.

    JINA_API_KEY=... python evals/retrieval/local_trace.py \\
        --corpus-dir /path/to/documents \\
        --queries /path/to/queries.txt \\
        --out /path/to/traces.md
    MISTRAL_API_KEY=... ...  --ocr        # OCR scanned PDFs via the production fallback
    ... --substitutions /path/to/subs.txt # fill <placeholder> slots in the queries
    ... --max-files 400 --sample-seed 7   # bound and spread a large directory

Queries are one per line; blank lines and ``#`` comments are ignored. A
query still holding an unsubstituted ``<placeholder>`` is skipped and
listed rather than searched literally, because a literal angle-bracket
search would enter the taxonomy as a fabricated failure.

Parsed chunks and their embeddings are cached under
``~/.cache/unstash-local-eval/chunks`` keyed on file content, so re-runs do
not re-pay parsing, OCR or embedding. The cache and the output file live
outside the corpus directory — nothing is ever written into the scanned
tree.

The output file reproduces document titles, paths and text. It is operator
working material: keep it outside version control.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import mimetypes
import os
import re
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND / "src"))

import numpy as np
from eval_db import fresh_database

SUFFIXES = {".pdf", ".docx", ".md", ".txt"}
# Mirrors the production ingestion defaults for the OCR fallback.
OCR_MIN_CHARS_PER_PAGE = 200
OCR_MAX_BYTES = 50 * 1024 * 1024
OCR_CACHE = Path.home() / ".cache" / "unstash-local-eval" / "ocr"
# Per-chunk texts, offsets and embeddings — a different shape from the
# pooled document vectors the clustering check caches, hence its own
# namespace under the same root.
CHUNK_CACHE = Path.home() / ".cache" / "unstash-local-eval" / "chunks"
PLACEHOLDER = re.compile(r"<[^<>]+>")
EMBED_BATCH = 64


def _find_files(corpus_dir: Path, max_files: int, sample_seed: int | None) -> list[Path]:
    """Supported files, either path-ordered or a seeded random sample.

    Path order concentrates on one subtree; a seeded sample spans the whole
    archive, which is the representative test.
    """
    all_files = sorted(p for p in corpus_dir.rglob("*") if p.suffix.lower() in SUFFIXES)
    if sample_seed is None or len(all_files) <= max_files:
        return all_files[:max_files]
    import random

    rng = random.Random(sample_seed)
    return sorted(rng.sample(all_files, max_files))


def load_substitutions(path: Path | None) -> dict[str, str]:
    """``<token>=value`` per line; blank lines and ``#`` comments ignored."""
    if path is None:
        return {}
    pairs: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        token, _, value = entry.partition("=")
        pairs[token.strip()] = value.strip()
    return pairs


def load_queries(path: Path, substitutions: dict[str, str]) -> tuple[list[str], list[str]]:
    """``(runnable, skipped)`` queries after placeholder substitution."""
    runnable: list[str] = []
    skipped: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        query = line.strip()
        if not query or query.startswith("#"):
            continue
        for token, value in substitutions.items():
            query = query.replace(token, value)
        (skipped if PLACEHOLDER.search(query) else runnable).append(query)
    return runnable, skipped


async def _parse_with_ocr(path: Path, use_ocr: bool):
    """Parsed chunks for one file; OCR fallback for scanned PDFs when enabled."""
    from unstash.documents.ocr import needs_ocr, ocr_pdf_to_markdown
    from unstash.documents.parser import parse_to_chunks

    parsed = await asyncio.to_thread(parse_to_chunks, path)
    scanned = path.suffix.lower() == ".pdf" and needs_ocr(
        page_count=parsed.page_count,
        total_chars=parsed.total_chars,
        min_chars_per_page=OCR_MIN_CHARS_PER_PAGE,
    )
    if not scanned:
        return parsed.chunks or None
    if not use_ocr:
        return None

    OCR_CACHE.mkdir(parents=True, exist_ok=True)
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
    return ocr_parsed.chunks or None


async def _embed_all(texts: list[str], embedder) -> list[list[float]]:
    """Embed in bounded batches; a long parse can exceed the provider's limit."""
    from unstash.documents.embedder import EmbeddingTask

    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = await embedder.embed(texts[start : start + EMBED_BATCH], task=EmbeddingTask.PASSAGE)
        vectors.extend(batch.vectors)
    return vectors


async def _load_document(path: Path, use_ocr: bool, embedder):
    """Chunk rows and vectors for one file, via the content-keyed cache."""
    from unstash.documents.ocr import OcrError

    CHUNK_CACHE.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(await asyncio.to_thread(path.read_bytes)).hexdigest()
    cached = CHUNK_CACHE / f"{digest}.npz"
    if cached.exists():
        stored = np.load(cached, allow_pickle=True)
        return (
            [str(t) for t in stored["texts"]],
            stored["starts"].tolist(),
            stored["ends"].tolist(),
            stored["tokens"].tolist(),
            stored["vectors"],
        )
    try:
        chunks = await _parse_with_ocr(path, use_ocr)
    except OcrError as exc:
        print(f"  skipped (OCR failed: {exc}): {path.name}", flush=True)
        return None
    # A sweep over thousands of real files meets malformed ones; any parse
    # failure is reported and skipped rather than ending the run.
    except Exception as exc:
        print(f"  skipped (parse failed: {type(exc).__name__}): {path.name}", flush=True)
        return None
    if not chunks:
        return None

    texts = [c.text for c in chunks]
    vectors = np.asarray(await _embed_all(texts, embedder), dtype=np.float32)
    starts = [c.char_offset_start for c in chunks]
    ends = [c.char_offset_end for c in chunks]
    tokens = [c.token_count for c in chunks]
    np.savez(
        cached,
        texts=np.array(texts, dtype=object),
        starts=np.array(starts),
        ends=np.array(ends),
        tokens=np.array(tokens),
        vectors=vectors,
    )
    return texts, starts, ends, tokens, vectors


async def index_corpus(
    pool,
    files: list[Path],
    corpus_dir: Path,
    *,
    use_ocr: bool,
    embedder,
) -> tuple[uuid.UUID, dict[uuid.UUID, str], int]:
    """Insert an org and every parseable file.

    Returns the org id, a document-to-relative-path map (the search hit
    carries no path, and the folder is load-bearing evidence when reading a
    trace), and the number of files skipped.
    """
    org_id = await pool.fetchval(
        "INSERT INTO organisations (slug, name) VALUES ('local', 'Local Corpus') RETURNING id"
    )
    paths: dict[uuid.UUID, str] = {}
    skipped = 0
    for position, path in enumerate(files, start=1):
        loaded = await _load_document(path, use_ocr, embedder)
        if loaded is None:
            skipped += 1
            continue
        texts, starts, ends, tokens, vectors = loaded
        relpath = str(path.relative_to(corpus_dir))
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        document_id = await pool.fetchval(
            "INSERT INTO documents (org_id, title, source_uri, mime_type, size_bytes,"
            " content_hash, status) VALUES ($1, $2, $3, $4, $5, $6, 'indexed') RETURNING id",
            org_id,
            path.name,
            relpath,
            mime,
            path.stat().st_size,
            hashlib.sha256(relpath.encode()).hexdigest(),
        )
        paths[document_id] = relpath
        for index, text in enumerate(texts):
            await pool.execute(
                "INSERT INTO chunks (org_id, document_id, chunk_index, text, token_count,"
                " char_offset_start, char_offset_end, embedding)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)",
                org_id,
                document_id,
                index,
                text,
                tokens[index],
                starts[index],
                ends[index],
                "[" + ",".join(str(v) for v in vectors[index]) + "]",
            )
        if position % 25 == 0:
            print(f"  {position}/{len(files)} files ...", flush=True)
    return org_id, paths, skipped


def _trace_block(
    number: int,
    query: str,
    outcome,
    paths: dict[uuid.UUID, str],
    top_k: int,
) -> str:
    """One query's trace, with a blank coding line for the reader to fill in."""
    lines = [
        f"## {number}. {query}",
        "",
        f"`results={len(outcome.hits)}` `reranked={outcome.reranked}` `bm25={outcome.bm25_used}`",
        "",
    ]
    if not outcome.hits:
        lines += ["_No results._", ""]
    for rank, hit in enumerate(outcome.hits[:top_k], start=1):
        rerank = "—" if hit.rerank_score is None else f"{hit.rerank_score:.3f}"
        # Snippets span chunk line breaks; a newline would end the blockquote
        # and render the remainder as body text or a list.
        snippet = " ".join(hit.snippet.split())
        lines += [
            f"**{rank}. {hit.title}** · `{hit.mime_type}` · fused {hit.score:.4f} · rerank {rerank}",
            f"  `{paths.get(hit.document_id, '?')}`",
            f"  > {snippet}",
            "",
        ]
    # First upstream failure only: a downstream cascade recorded as several
    # codes inflates every count derived from this file.
    lines += ["`first_failure:` ", "", "---", ""]
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> None:
    substitutions = load_substitutions(args.substitutions)
    runnable, skipped_queries = load_queries(args.queries, substitutions)
    if not runnable:
        sys.exit("no runnable queries after substitution")
    files = _find_files(args.corpus_dir, args.max_files, args.sample_seed)
    if not files:
        sys.exit(f"no supported files under {args.corpus_dir}")

    # The provider clients are built by the production factories from
    # Settings, so backend choice is expressed the way the app expresses it.
    os.environ["UNSTASH_EMBEDDER_BACKEND"] = args.embedder
    os.environ["UNSTASH_RERANKER_BACKEND"] = args.reranker
    if "JINA_API_KEY" in os.environ:
        os.environ.setdefault("jina_api_key", os.environ["JINA_API_KEY"])

    print(f"indexing {len(files)} files ...", flush=True)
    async with fresh_database() as pool:
        from unstash.config import get_settings
        from unstash.documents.embedder import get_embedder
        from unstash.search.reranker import get_reranker
        from unstash.search.service import run_search
        from unstash.tasks.context import org_context

        settings = get_settings()
        embedder = get_embedder(settings)
        reranker = get_reranker(settings)

        org_id, paths, skipped_files = await index_corpus(
            pool, files, args.corpus_dir, use_ocr=args.ocr, embedder=embedder
        )
        chunks = await pool.fetchval("SELECT count(*) FROM chunks")
        print(
            f"indexed {len(paths)} documents ({chunks} chunks), {skipped_files} skipped",
            flush=True,
        )

        blocks = []
        for number, query in enumerate(runnable, start=1):
            async with org_context(org_id) as session:
                outcome = await run_search(
                    session,
                    org_id=org_id,
                    query=query,
                    embedder=embedder,
                    reranker=reranker,
                    settings=settings,
                )
            blocks.append(_trace_block(number, query, outcome, paths, args.top_k))
            if number % 10 == 0:
                print(f"  {number}/{len(runnable)} queries ...", flush=True)

    header = [
        "# Retrieval traces for error analysis",
        "",
        f"{len(paths)} documents ({chunks} chunks) · {len(runnable)} queries "
        f"· embedder={args.embedder} · reranker={args.reranker} · ocr={args.ocr}",
        "",
        "Fill `first_failure:` with the **first upstream** failure only. Leave it "
        "blank when the result set is adequate.",
        "",
    ]
    if skipped_queries:
        header += [
            f"{len(skipped_queries)} queries skipped for unsubstituted placeholders:",
            "",
            *(f"- {q}" for q in skipped_queries),
            "",
        ]
    header += ["---", ""]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(header) + "\n".join(blocks), encoding="utf-8")
    print(f"\nwritten to {args.out}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--substitutions", type=Path)
    parser.add_argument("--embedder", choices=("jina", "fake"), default="jina")
    parser.add_argument("--reranker", choices=("jina", "fake"), default="jina")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--max-files", type=int, default=10_000)
    parser.add_argument("--sample-seed", type=int)
    parser.add_argument("--top-k", type=int, default=10)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
