"""Tests for the parser's chunk bookkeeping.

Docling itself is exercised by the integration suite against real
files. These tests stub the converter and chunker to verify the logic
that is ours: chunk indexing, offset accounting, token-count
delegation, and provenance fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unstash.documents import parser
from unstash.documents.parser import PIPELINE_VERSION, parse_to_chunks

if TYPE_CHECKING:
    import pytest


@dataclass
class _FakeChunk:
    text: str


class _FakeTokenizer:
    """Counts a 'token' per character, so expectations are trivial."""

    def count_tokens(self, text: str) -> int:
        return len(text)


class _FakeChunker:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.tokenizer = _FakeTokenizer()

    def chunk(self, doc: Any) -> list[_FakeChunk]:
        return [_FakeChunk(text) for text in self._texts]


class _FakeDoc:
    def num_pages(self) -> int:
        return 3


class _FakeConvertResult:
    document = _FakeDoc()


class _FakeConverter:
    def convert(self, path: str) -> _FakeConvertResult:
        return _FakeConvertResult()


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    chunk_texts: list[str],
) -> None:
    monkeypatch.setattr(parser, "_get_converter", _FakeConverter)
    monkeypatch.setattr(parser, "_get_chunker", lambda: _FakeChunker(chunk_texts))


def test_chunk_indices_and_offsets_are_contiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    texts = ["first chunk", "second, longer chunk of text", "tail"]
    _patch_pipeline(monkeypatch, texts)

    parsed = parse_to_chunks(Path("ignored.pdf"))

    assert [c.chunk_index for c in parsed.chunks] == [0, 1, 2]
    assert [c.text for c in parsed.chunks] == texts

    cursor = 0
    for chunk, text in zip(parsed.chunks, texts, strict=True):
        assert chunk.char_offset_start == cursor
        assert chunk.char_offset_end == cursor + len(text)
        cursor += len(text)


def test_token_count_is_delegated_to_the_tokenizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    texts = ["abc", "abcdef"]
    _patch_pipeline(monkeypatch, texts)

    parsed = parse_to_chunks(Path("ignored.pdf"))

    # The fake tokenizer counts one token per character.
    assert [c.token_count for c in parsed.chunks] == [3, 6]


def test_empty_document_produces_no_chunks_but_full_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, [])

    parsed = parse_to_chunks(Path("ignored.pdf"))

    assert parsed.chunks == []
    assert parsed.pipeline_version == PIPELINE_VERSION
    assert set(parsed.pipeline_config) == {
        "tokenizer_model",
        "chunk_target_tokens",
        "do_ocr",
        "do_table_structure",
    }


def test_pipeline_config_records_the_actual_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch, ["one chunk"])

    parsed = parse_to_chunks(Path("ignored.pdf"))

    config = parsed.pipeline_config
    assert config["chunk_target_tokens"] == 500
    assert config["do_ocr"] is False
    assert config["do_table_structure"] is True
