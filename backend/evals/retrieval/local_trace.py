"""Retrieval traces over a local document directory, for hand-read error analysis.

Indexes an operator-provided directory into a fresh Postgres at migration
head using the production parser, OCR fallback and embedder, then runs a
list of real queries through the production search pipeline
(:func:`unstash.search.service.run_search`) and writes the result as
structured data for the annotation tool to render.

Output is three files. ``traces.jsonl`` holds one query per line, each with
its results *and* the intermediate rankings behind them — which leg found a
document, and where it sat before the reranker. ``<name>-run.json`` holds
what is true of the whole run, including the document-to-paths map.
``<name>-manifest.jsonl`` records what became of every corpus file.

This is the instrument for error analysis: the step that produces a failure
taxonomy from observed traces rather than a metric from a golden set. It
deliberately reports **no scores**. There are no relevance judgments for
these queries, and a number computed without them would be invented.

    JINA_API_KEY=... python evals/retrieval/local_trace.py \\
        --corpus-dir /path/to/documents \\
        --queries /path/to/queries.txt \\
        --out /path/to/traces.jsonl
    MISTRAL_API_KEY=... ...  --ocr        # OCR scanned PDFs via the production fallback
    ... --substitutions /path/to/subs.txt # fill <placeholder> slots in the queries
    ... --gotenberg-url http://localhost:3000  # convert legacy office formats
    ... --max-files 400 --sample-seed 7   # bound and spread a large directory

Which files are indexed is decided by the production strategy router, not by
a suffix list here, so the traces reflect what the product can reach. Files
the router sends to metadata-only or skip appear in the manifest with that
reason: a reader coding a miss needs to know whether the document was in the
index at all, and a file that was never eligible is not a retrieval failure.
Legacy office formats need the Gotenberg sidecar; without ``--gotenberg-url``
they are recorded as failed conversions rather than silently missing.

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
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND / "src"))

import numpy as np
from eval_db import fresh_database

# Mirrors the production ingestion defaults for the OCR fallback.
OCR_MIN_CHARS_PER_PAGE = 200
OCR_MAX_BYTES = 50 * 1024 * 1024
CONVERT_MAX_BYTES = 50 * 1024 * 1024
OCR_CACHE = Path.home() / ".cache" / "unstash-local-eval" / "ocr"
# Per-chunk texts, offsets and embeddings — a different shape from the
# pooled document vectors the clustering check caches, hence its own
# namespace under the same root.
CHUNK_CACHE = Path.home() / ".cache" / "unstash-local-eval" / "chunks"
PLACEHOLDER = re.compile(r"<[^<>]+>")
EMBED_BATCH = 64
# Backoff before each retry of a document's embedding call; one final attempt
# follows the last delay.
EMBED_RETRY_DELAYS = (5, 20, 60)
# Measured over 800 documents of this corpus (5.75M chars / 2.33M tokens from
# the chunker's own tokenizer). Swedish compounds fragment heavily, so the
# common four-characters-per-token assumption understates rerank payloads by
# ~40% — enough to blow a token budget that looks comfortable on paper.
CHARS_PER_TOKEN = 2.46
OCR_RETRY_DELAYS = (5, 20, 60)
# OcrError messages that describe a settled outcome rather than a transient one.
OCR_TERMINAL_MARKERS = (
    "produced no text",
    "over the",
    "Could not read source file",
)


@dataclass(frozen=True, slots=True)
class _LoadOptions:
    """Per-file handling knobs threaded through the ingest loop."""

    use_ocr: bool
    gotenberg_url: str | None
    tmp_dir: Path
    budget: _TokenBudget


@dataclass(frozen=True, slots=True)
class _Selection:
    """What the router chose, what it declined, and what sampling dropped."""

    files: list[Path]
    declined: dict[str, int]
    declined_files: list[tuple[str, str]]
    chunkable_total: int

    @property
    def sampled(self) -> bool:
        """True when ``--max-files`` held the run below the chunkable set."""
        return len(self.files) < self.chunkable_total


def _find_files(
    corpus_dir: Path,
    max_files: int,
    sample_seed: int | None,
) -> _Selection:
    """Chunkable files plus a census of what the router declined.

    Selection goes through the production strategy router rather than a
    local suffix list, so the trace reflects what the product can index
    rather than what this script happens to recognise. Path order
    concentrates on one subtree; a seeded sample spans the whole archive,
    which is the representative test.

    The declined census counts the whole directory even when ``max_files``
    bounds the run, so the two figures are only comparable on a full run —
    :attr:`_Selection.sampled` marks when they are not.
    """
    from unstash.documents.mime import detect_mime
    from unstash.documents.strategy import ParseStrategy, select_strategy

    chunkable: list[Path] = []
    declined: dict[str, int] = {}
    declined_files: list[tuple[str, str]] = []
    for path in sorted(corpus_dir.rglob("*")):
        if not path.is_file():
            continue
        strategy = select_strategy(detect_mime(path))
        if strategy in (ParseStrategy.EXTRACT, ParseStrategy.CONVERT_THEN_EXTRACT):
            chunkable.append(path)
        else:
            declined[strategy.value] = declined.get(strategy.value, 0) + 1
            declined_files.append((str(path.relative_to(corpus_dir)), strategy.value))

    total = len(chunkable)
    if sample_seed is None or total <= max_files:
        return _Selection(chunkable[:max_files], declined, declined_files, total)
    import random

    rng = random.Random(sample_seed)
    return _Selection(sorted(rng.sample(chunkable, max_files)), declined, declined_files, total)


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


async def _converted_pdf(path: Path, gotenberg_url: str, tmp_dir: Path) -> Path:
    """Legacy office file converted to a PDF beside the cache, never in the tree."""
    from unstash.documents.conversion import convert_to_pdf

    pdf = await convert_to_pdf(
        path,
        gotenberg_url=gotenberg_url,
        max_bytes=CONVERT_MAX_BYTES,
        timeout=180.0,
    )
    target = tmp_dir / f"{hashlib.sha256(str(path).encode()).hexdigest()}.pdf"
    await asyncio.to_thread(target.write_bytes, pdf)
    return target


def _ocr_can_retry(message: str) -> bool:
    """Whether re-asking could plausibly give a different answer.

    ``OcrError`` carries every failure mode as one type, so the message is
    the only signal available. A document with no text layer, one over the
    size limit and one that cannot be read all fail identically on a second
    attempt; a 4xx other than 429 is likewise a settled answer.
    """
    if any(marker in message for marker in OCR_TERMINAL_MARKERS):
        return False
    status = re.search(r"OCR API returned (\d{3})", message)
    if status and not status.group(1).startswith("5"):
        return status.group(1) == "429"
    return True


async def _ocr_once(path: Path) -> str:
    """One OCR call against the production client."""
    from unstash.documents.ocr import ocr_pdf_to_markdown

    return await ocr_pdf_to_markdown(
        path,
        api_key=os.environ["MISTRAL_API_KEY"],
        base_url="https://api.mistral.ai",
        model="mistral-ocr-latest",
        max_bytes=OCR_MAX_BYTES,
        timeout=120.0,
    )


async def _ocr_with_retry(path: Path) -> str:
    """OCR one PDF, retrying provider-side failures; raises when out of attempts."""
    from unstash.documents.ocr import OcrError

    for attempt, delay in enumerate(OCR_RETRY_DELAYS, start=1):
        try:
            return await _ocr_once(path)
        except OcrError as exc:
            if not _ocr_can_retry(str(exc)):
                raise
            print(
                f"  OCR attempt {attempt}/{len(OCR_RETRY_DELAYS)} failed ({exc}), "
                f"retrying in {delay}s: {path.name}",
                flush=True,
            )
            await asyncio.sleep(delay)
    return await _ocr_once(path)


async def _parse_with_ocr(path: Path, use_ocr: bool):
    """Parsed chunks for one file; OCR fallback for scanned PDFs when enabled."""
    from unstash.documents.ocr import needs_ocr
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
        markdown = await _ocr_with_retry(path)
        await asyncio.to_thread(cached.write_text, markdown, "utf-8")
    ocr_parsed = await asyncio.to_thread(parse_to_chunks, cached)
    return ocr_parsed.chunks or None


class _TokenBudget:
    """Sliding-window limiter over the provider's tokens-per-minute cap.

    The corpus is mostly small documents — a median of ~900 tokens — with a
    tail of spreadsheets and long reports that chunk into hundreds of pieces.
    One of those issues several large requests back to back and can exceed a
    whole minute's allowance on its own, which is what provokes the limit;
    average throughput sits far below it. Waiting before the request keeps
    the tail from bursting, where retrying after a rejection does not.
    """

    def __init__(self, tokens_per_minute: int) -> None:
        self._cap = tokens_per_minute
        self._window: deque[tuple[float, int]] = deque()

    def _spent(self, now: float) -> int:
        while self._window and now - self._window[0][0] >= 60.0:
            self._window.popleft()
        return sum(tokens for _, tokens in self._window)

    async def take(self, tokens: int) -> None:
        """Block until ``tokens`` fit in the trailing minute, then record them."""
        while True:
            now = time.monotonic()
            spent = self._spent(now)
            # A single request larger than the cap can never fit; let it
            # through on an empty window rather than deadlock.
            if spent + tokens <= self._cap or not self._window:
                self._window.append((now, tokens))
                return
            wait = 60.0 - (now - self._window[0][0])
            await asyncio.sleep(max(wait, 0.1))


class _PacedReranker:
    """Reranker wrapper that draws from the same provider token budget.

    Rerank shares the embedding key's allowance and is the heavier of the two
    per call: one request carries the query plus every candidate excerpt. Left
    unpaced against a swept query list it exhausts the minute's tokens quickly,
    and the failure is quiet — a rerank error is non-fatal by design, so the
    request degrades to fusion order and the trace still looks complete.

    Token count is estimated from character length, since the provider is
    handed raw strings here. ``CHARS_PER_TOKEN`` is measured on this corpus
    rather than assumed: Swedish compounds fragment far more than the usual
    four-characters-per-token rule of thumb.
    """

    def __init__(self, inner, budget: _TokenBudget) -> None:
        self._inner = inner
        self._budget = budget

    async def rerank(self, query: str, documents: list[str]):
        """Wait for budget, then delegate to the wrapped reranker."""
        chars = len(query) + sum(len(d) for d in documents)
        await self._budget.take(int(chars / CHARS_PER_TOKEN) + 1)
        return await self._inner.rerank(query, documents)


async def _embed_all(
    texts: list[str],
    token_counts: list[int],
    embedder,
    budget: _TokenBudget,
) -> list[list[float]]:
    """Embed in bounded batches, pacing against the provider's token budget."""
    from unstash.documents.embedder import EmbeddingTask

    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        window = slice(start, start + EMBED_BATCH)
        await budget.take(sum(token_counts[window]))
        batch = await embedder.embed(texts[window], task=EmbeddingTask.PASSAGE)
        vectors.extend(batch.vectors)
    return vectors


async def _embed_with_retry(
    texts: list[str],
    token_counts: list[int],
    embedder,
    budget: _TokenBudget,
    path: Path,
) -> list[list[float]] | None:
    """Embed one document's chunks, retrying transient provider failures.

    Pacing is the primary defence; this is the backstop for what pacing
    cannot predict — a 5xx, a dropped connection, or another client sharing
    the key's allowance.
    """
    for attempt, delay in enumerate(EMBED_RETRY_DELAYS, start=1):
        try:
            return await _embed_all(texts, token_counts, embedder, budget)
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            print(
                f"  embed attempt {attempt}/{len(EMBED_RETRY_DELAYS)} failed ({last}), "
                f"retrying in {delay}s: {path.name}",
                flush=True,
            )
            await asyncio.sleep(delay)
    try:
        return await _embed_all(texts, token_counts, embedder, budget)
    except Exception as exc:
        print(f"  skipped (embed failed: {type(exc).__name__}): {path.name}", flush=True)
        return None


async def _source_for_parse(path: Path, opts: _LoadOptions) -> Path | None:
    """The file to hand the parser: the original, or its converted PDF."""
    from unstash.documents.conversion import ConversionError
    from unstash.documents.mime import detect_mime
    from unstash.documents.strategy import ParseStrategy, select_strategy

    if select_strategy(detect_mime(path)) is not ParseStrategy.CONVERT_THEN_EXTRACT:
        return path
    if opts.gotenberg_url is None:
        print(f"  skipped (needs conversion, no sidecar): {path.name}", flush=True)
        return None
    try:
        return await _converted_pdf(path, opts.gotenberg_url, opts.tmp_dir)
    except ConversionError as exc:
        print(f"  skipped (conversion failed: {exc}): {path.name}", flush=True)
        return None


async def _load_document(path: Path, digest: str, opts: _LoadOptions, embedder):
    """Chunk rows and vectors for one file, via the content-keyed cache."""
    CHUNK_CACHE.mkdir(parents=True, exist_ok=True)
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
    return await _parse_and_embed(path, cached, opts, embedder)


async def _parse_and_embed(path: Path, cached: Path, opts: _LoadOptions, embedder):
    """Parse, embed and cache one uncached file; ``None`` when it cannot be read."""
    from unstash.documents.ocr import OcrError

    source = await _source_for_parse(path, opts)
    if source is None:
        return None
    try:
        chunks = await _parse_with_ocr(source, opts.use_ocr)
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
    tokens = [c.token_count for c in chunks]
    embedded = await _embed_with_retry(texts, tokens, embedder, opts.budget, path)
    if embedded is None:
        return None
    vectors = np.asarray(embedded, dtype=np.float32)
    starts = [c.char_offset_start for c in chunks]
    ends = [c.char_offset_end for c in chunks]
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
    opts: _LoadOptions,
    embedder,
) -> tuple[uuid.UUID, dict[uuid.UUID, list[str]], list[dict]]:
    """Insert an org and every parseable file, deduplicated by content.

    Identical bytes filed in two folders become one document, as in the
    upload path, which keys ``content_hash`` on content and returns the
    existing row. Indexing each copy separately would put the same document
    in a result list several times and read as a ranking fault.

    Returns the org id, a document-to-paths map (the search hit carries no
    path, and the folder is load-bearing evidence when reading a trace), and a
    manifest recording what became of every file. The manifest exists because
    "no result for this document" has four different causes — indexed,
    collapsed into an identical copy, failed to parse, or never eligible — and
    a reviewer who cannot tell them apart will attribute all four to retrieval.
    """
    org_id = await pool.fetchval(
        "INSERT INTO organisations (slug, name) VALUES ('local', 'Local Corpus') RETURNING id"
    )
    paths: dict[uuid.UUID, list[str]] = {}
    by_digest: dict[str, uuid.UUID] = {}
    manifest: list[dict] = []
    for position, path in enumerate(files, start=1):
        relpath = str(path.relative_to(corpus_dir))
        digest = hashlib.sha256(await asyncio.to_thread(path.read_bytes)).hexdigest()
        if digest in by_digest:
            paths[by_digest[digest]].append(relpath)
            manifest.append(
                {
                    "path": relpath,
                    "status": "duplicate",
                    "document_id": str(by_digest[digest]),
                    "same_content_as": paths[by_digest[digest]][0],
                }
            )
            continue
        loaded = await _load_document(path, digest, opts, embedder)
        if loaded is None:
            manifest.append({"path": relpath, "status": "failed", "document_id": None})
            continue
        texts, starts, ends, tokens, vectors = loaded
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        document_id = await pool.fetchval(
            "INSERT INTO documents (org_id, title, source_uri, mime_type, size_bytes,"
            " content_hash, status) VALUES ($1, $2, $3, $4, $5, $6, 'indexed') RETURNING id",
            org_id,
            path.name,
            relpath,
            mime,
            path.stat().st_size,
            digest,
        )
        by_digest[digest] = document_id
        paths[document_id] = [relpath]
        manifest.append({"path": relpath, "status": "indexed", "document_id": str(document_id)})
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
    return org_id, paths, manifest


def _query_record(number: int, query: str, outcome, top_k: int) -> dict:
    """One query as structured data, including where each result came from.

    The returned list is the end of the pipeline; on its own it cannot
    distinguish a document neither leg retrieved from one the reranker
    demoted. ``run_search(explain=True)`` carries the intermediate rankings,
    and they are recorded here rather than flattened away.

    ``candidates`` is the full fused ordering, not the shown results — the
    question "where did the document I expected actually rank" is normally
    answered outside the top k.
    """
    detail = outcome.explain
    hits = []
    for rank, hit in enumerate(outcome.hits[:top_k], start=1):
        prov = detail.provenance.get(hit.document_id) if detail else None
        hits.append(
            {
                "rank": rank,
                "document_id": str(hit.document_id),
                "title": hit.title,
                "mime": hit.mime_type,
                "fused": hit.score,
                "rerank": hit.rerank_score,
                "snippet": hit.snippet,
                "excerpt": hit.excerpt,
                "vector_rank": prov.vector_rank if prov else None,
                "bm25_rank": prov.bm25_rank if prov else None,
                "bm25_score": prov.bm25_score if prov else None,
                "fused_rank": prov.fused_rank if prov else None,
            }
        )
    record = {
        "n": number,
        "query": query,
        "results": len(outcome.hits),
        "reranked": outcome.reranked,
        "bm25_used": outcome.bm25_used,
        "failed": None,
        "hits": hits,
    }
    if detail:
        record["candidates"] = [
            {"document_id": str(doc), "fused": score, "fused_rank": i}
            for i, (doc, score) in enumerate(detail.candidates, start=1)
        ]
        record["vector_pool"] = {str(doc): rank for doc, rank in detail.vector_pool.items()}
        record["bm25_pool"] = {
            str(doc): {"rank": rank, "score": score}
            for doc, (rank, score) in detail.bm25_pool.items()
        }
    return record


def _error_record(number: int, query: str, detail: str) -> dict:
    """A query the pipeline could not answer at all.

    Carries ``failed`` so a reader never codes a transport error as a
    retrieval result.
    """
    return {
        "n": number,
        "query": query,
        "results": 0,
        "reranked": False,
        "bm25_used": False,
        "failed": detail,
        "hits": [],
    }


async def run(args: argparse.Namespace) -> None:
    substitutions = load_substitutions(args.substitutions)
    runnable, skipped_queries = load_queries(args.queries, substitutions)
    if not runnable:
        sys.exit("no runnable queries after substitution")
    selection = _find_files(args.corpus_dir, args.max_files, args.sample_seed)
    if not selection.files:
        sys.exit(f"no chunkable files under {args.corpus_dir}")

    # The provider clients are built by the production factories from
    # Settings, so backend choice is expressed the way the app expresses it.
    os.environ["UNSTASH_EMBEDDER_BACKEND"] = args.embedder
    os.environ["UNSTASH_RERANKER_BACKEND"] = args.reranker
    if "JINA_API_KEY" in os.environ:
        os.environ.setdefault("jina_api_key", os.environ["JINA_API_KEY"])

    print(f"indexing {len(selection.files)} files ...", flush=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="unstash-local-trace-"))
    opts = _LoadOptions(
        use_ocr=args.ocr,
        gotenberg_url=args.gotenberg_url,
        tmp_dir=tmp_dir,
        budget=_TokenBudget(args.tpm),
    )
    async with fresh_database() as pool:
        from unstash.config import get_settings
        from unstash.documents.embedder import get_embedder
        from unstash.search.reranker import get_reranker
        from unstash.search.service import run_search
        from unstash.tasks.context import org_context

        settings = get_settings()
        embedder = get_embedder(settings)
        reranker = _PacedReranker(get_reranker(settings), opts.budget)

        org_id, paths, manifest = await index_corpus(
            pool, selection.files, args.corpus_dir, opts=opts, embedder=embedder
        )
        chunks = await pool.fetchval("SELECT count(*) FROM chunks")
        skipped_files = sum(1 for row in manifest if row["status"] == "failed")
        print(
            f"indexed {len(paths)} documents ({chunks} chunks), {skipped_files} skipped",
            flush=True,
        )

        records = []
        for number, query in enumerate(runnable, start=1):
            # Hours of indexing precede this loop and the report is written
            # after it, so one failed query must not discard the rest.
            try:
                async with org_context(org_id) as session:
                    outcome = await run_search(
                        session,
                        org_id=org_id,
                        query=query,
                        embedder=embedder,
                        reranker=reranker,
                        settings=settings,
                        explain=True,
                    )
            except Exception as exc:
                records.append(_error_record(number, query, f"{type(exc).__name__}: {exc}"))
                print(f"  query {number} failed ({type(exc).__name__})", flush=True)
                continue
            records.append(_query_record(number, query, outcome, args.top_k))
            if number % 10 == 0:
                print(f"  {number}/{len(runnable)} queries ...", flush=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    _write_outputs(
        args,
        selection,
        records,
        skipped_queries,
        _Counts(len(paths), chunks, skipped_files),
        documents={str(doc): {"paths": p} for doc, p in paths.items()},
        manifest=manifest,
    )


@dataclass(frozen=True, slots=True)
class _Counts:
    """What the indexing pass produced, for the report header."""

    documents: int
    chunks: int
    skipped_files: int


def _write_outputs(
    args: argparse.Namespace,
    selection: _Selection,
    records: list[dict],
    skipped_queries: list[str],
    counts: _Counts,
    documents: dict[str, dict],
    manifest: list[dict],
) -> None:
    """Write the run in three files, each answering a different question.

    ``traces.jsonl`` — one query per line. Structured rather than rendered:
    an earlier markdown format was parsed with regular expressions and
    silently dropped 28% of results when rerank scores turned out to be
    signed. A reader that cannot fail loudly is worse than no reader.

    ``run.json`` — everything true of the whole run, plus the document map.
    Paths live here once instead of being repeated in every query record.

    ``manifest.jsonl`` — what became of each corpus file. A reviewer looking
    for a document that never appeared needs to distinguish indexed, collapsed
    into a duplicate, failed to parse, and never eligible.
    """
    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    declined = dict(sorted(selection.declined.items()))
    meta = {
        "traces": str(out),
        "corpus_dir": str(args.corpus_dir),
        "queries_file": str(args.queries),
        "embedder": args.embedder,
        "reranker": args.reranker,
        "ocr": args.ocr,
        "top_k": args.top_k,
        "documents_indexed": counts.documents,
        "chunks": counts.chunks,
        "files_failed": counts.skipped_files,
        "declined_by_router": declined,
        "sampled": selection.sampled,
        "files_selected": len(selection.files),
        "chunkable_total": selection.chunkable_total,
        "sample_seed": args.sample_seed,
        "queries_run": len(records),
        "queries_skipped_placeholders": skipped_queries,
        "documents": documents,
    }
    meta_path = out.with_name(out.stem + "-run.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = out.with_name(out.stem + "-manifest.jsonl")
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in manifest:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        for path, router in selection.declined_files:
            handle.write(
                json.dumps(
                    {
                        "path": path,
                        "status": "not-indexed",
                        "document_id": None,
                        "router": router,
                        "reason": _NOT_INDEXED_REASON.get(router, router),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(f"\nwritten:\n  {out}\n  {meta_path}\n  {manifest_path}", flush=True)


# Why a file the router declined has no searchable content *today*. These are
# gaps with open work behind them, not properties of the files: the product
# intent is that everything in the corpus is reachable by some means.
_NOT_INDEXED_REASON = {
    "metadata_only": (
        "no text extracted yet — images need OCR (#186), archives need expanding (#185)"
    ),
    "skip": "format refused by the router (#187)",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--substitutions", type=Path)
    parser.add_argument("--embedder", choices=("jina", "fake"), default="jina")
    parser.add_argument("--reranker", choices=("jina", "fake"), default="jina")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument(
        "--gotenberg-url",
        help="Sidecar base URL for legacy office conversion, e.g. http://localhost:3000. "
        "Without it those files are counted and left unindexed.",
    )
    parser.add_argument(
        "--tpm",
        type=int,
        default=80_000,
        help="Embedding tokens per minute to pace to. Default leaves headroom under "
        "the 100k free-tier cap; raise it for a paid key.",
    )
    parser.add_argument("--max-files", type=int, default=10_000)
    parser.add_argument("--sample-seed", type=int)
    parser.add_argument("--top-k", type=int, default=10)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
