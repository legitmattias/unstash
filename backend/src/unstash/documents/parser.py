"""Docling-backed parser + HybridChunker integration.

Phase B of M3: given a path to a file that the strategy router has
marked as ``EXTRACT``, parse it with Docling, run the HybridChunker,
and return a list of chunks ready for database insertion. The chunks
have no embedding at this stage — Phase C (M3-C) fills those in.

Heavy objects (the Docling converter and the chunker's tokenizer) are
constructed lazily once per process and reused. The first call pays
the model-load cost; subsequent calls are fast. In production the
Dockerfile pre-caches the Docling artefacts so even the first call
does not have to hit the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from transformers import AutoTokenizer

if TYPE_CHECKING:
    from pathlib import Path

# Tokenizer used purely for token counting and chunk sizing. The full
# embedding model (Jina v4) tokenizes slightly differently, but for
# chunk-size targets a small reusable tokenizer is good enough — the
# milestone aims for "~500 tokens, ~50 overlap" not bit-exact bounds.
_TOKENIZER_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_CHUNK_TARGET_TOKENS = 500

# Free-form provenance string written to ``documents.pipeline_version``
# so later code can tell which pipeline produced which chunks. Bumped
# when the parser, the chunker, or the chunking parameters change in a
# way that should invalidate downstream artefacts (PII redaction,
# eval golden sets, etc.).
PIPELINE_VERSION = "docling@2.81 chunker=hybrid tokenizer=miniLM-L6-v2 v1"


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    """A single chunk produced by the parser, ready for DB insertion."""

    chunk_index: int
    text: str
    token_count: int
    char_offset_start: int
    char_offset_end: int


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Result of parsing one document."""

    chunks: list[ParsedChunk]
    pipeline_version: str
    pipeline_config: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _get_converter() -> DocumentConverter:
    """Build (or return cached) DocumentConverter.

    OCR is disabled by default — Phase E adds it as an opt-in path
    triggered by a "low text density per page" heuristic. Table
    structure detection stays on (BRF protocols have meaningful
    tables; turning it off would lose information).
    """
    opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
        },
    )


@lru_cache(maxsize=1)
def _get_chunker() -> HybridChunker:
    """Build (or return cached) HybridChunker with our tokenizer."""
    # transformers ships without complete type stubs; the runtime call
    # is well-defined and exercised by the integration tests.
    tokenizer = HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(_TOKENIZER_MODEL),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        max_tokens=_CHUNK_TARGET_TOKENS,
    )
    return HybridChunker(tokenizer=tokenizer, merge_peers=True)


def parse_to_chunks(file_path: Path) -> ParsedDocument:
    """Parse a file at ``file_path`` and return its chunks.

    Raises whatever Docling raises on a corrupt or unreadable file;
    the worker is expected to catch and translate to a
    ``documents.status = 'failed'`` transition.

    The returned chunk offsets (``char_offset_start`` /
    ``char_offset_end``) are positions in the *chunked* text stream,
    not into the original binary source. That's fine for retrieval
    highlighting within the chunk; mapping back to the source binary
    would need page-level provenance from Docling's ``doc_items``
    metadata and is deferred.
    """
    converter = _get_converter()
    chunker = _get_chunker()

    result = converter.convert(str(file_path))
    doc = result.document

    chunks: list[ParsedChunk] = []
    cursor = 0
    for index, chunk in enumerate(chunker.chunk(doc)):
        text = chunk.text
        start = cursor
        end = cursor + len(text)
        token_count = chunker.tokenizer.count_tokens(text)
        chunks.append(
            ParsedChunk(
                chunk_index=index,
                text=text,
                token_count=token_count,
                char_offset_start=start,
                char_offset_end=end,
            ),
        )
        cursor = end

    config = {
        "tokenizer_model": _TOKENIZER_MODEL,
        "chunk_target_tokens": _CHUNK_TARGET_TOKENS,
        "do_ocr": False,
        "do_table_structure": True,
    }
    return ParsedDocument(
        chunks=chunks,
        pipeline_version=PIPELINE_VERSION,
        pipeline_config=config,
    )
