"""Smoke test for the chunks DiskANN + pg_search indexes.

Both indexes are created via raw SQL in migration 0002 and are queried
by the hybrid-search path. This test proves they answer queries —
catching an opclass/dimension or ICU-tokenizer misconfiguration before
search is built on top of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from unstash.db.models.chunk import EMBEDDING_DIM

if TYPE_CHECKING:
    import uuid

    import asyncpg


def _one_hot(index: int) -> str:
    """An EMBEDDING_DIM one-hot vector as a pgvector text literal."""
    values = ["0"] * EMBEDDING_DIM
    values[index] = "1"
    return "[" + ",".join(values) + "]"


async def _seed_two_chunks(pool: asyncpg.Pool) -> uuid.UUID:
    """Insert an org, a document, and two chunks with distinct vectors/text."""
    async with pool.acquire() as conn:
        org_id = await conn.fetchval(
            "INSERT INTO organisations (slug, name) VALUES ($1, $2) RETURNING id",
            "smoke",
            "Smoke Org",
        )
        document_id = await conn.fetchval(
            "INSERT INTO documents "
            "(org_id, title, source_uri, mime_type, size_bytes, content_hash) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            org_id,
            "protokoll.pdf",
            "/smoke/protokoll.pdf",
            "application/pdf",
            123,
            "smoke-hash",
        )
        rows = [
            (0, "Styrelsen beslutade att renovera taket på fastigheten.", _one_hot(0)),
            (1, "Årsredovisning och budget för föreningens ekonomi.", _one_hot(1)),
        ]
        for chunk_index, chunk_text, embedding in rows:
            await conn.execute(
                "INSERT INTO chunks "
                "(org_id, document_id, chunk_index, text, token_count, "
                "char_offset_start, char_offset_end, embedding) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)",
                org_id,
                document_id,
                chunk_index,
                chunk_text,
                8,
                0,
                len(chunk_text),
                embedding,
            )
        return org_id


async def test_specialised_indexes_exist_with_expected_access_methods(
    migrations_pool: asyncpg.Pool,
) -> None:
    """The DiskANN and BM25 indexes exist and use the expected access methods."""
    async with migrations_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT i.relname AS index_name, am.amname AS access_method "
            "FROM pg_class i "
            "JOIN pg_index ix ON ix.indexrelid = i.oid "
            "JOIN pg_am am ON am.oid = i.relam "
            "WHERE i.relname IN ('ix_chunks_embedding_diskann', 'ix_chunks_text_bm25')",
        )
    methods = {r["index_name"]: r["access_method"] for r in rows}
    assert methods.get("ix_chunks_embedding_diskann") == "diskann"
    assert methods.get("ix_chunks_text_bm25") == "bm25"


async def test_vector_cosine_query_returns_nearest_chunk(
    migrations_pool: asyncpg.Pool,
) -> None:
    """A cosine nearest-neighbour query returns the matching chunk.

    Exercises the vector column's dimension and the ``vector_cosine_ops``
    opclass the DiskANN index is built on.
    """
    org_id = await _seed_two_chunks(migrations_pool)
    async with migrations_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT chunk_index, embedding <=> $2::vector AS distance "
            "FROM chunks WHERE org_id = $1 "
            "ORDER BY embedding <=> $2::vector LIMIT 1",
            org_id,
            _one_hot(0),
        )
    assert row is not None
    assert row["chunk_index"] == 0
    assert row["distance"] < 0.001


async def test_bm25_match_query_returns_matching_chunk(
    migrations_pool: asyncpg.Pool,
) -> None:
    """A BM25 full-text match returns only the chunk containing the term.

    The ``@@@`` operator is served exclusively by the pg_search index, so a
    correct result confirms the index and its ICU tokenizer are working.
    """
    org_id = await _seed_two_chunks(migrations_pool)
    async with migrations_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chunk_index FROM chunks WHERE org_id = $1 AND text @@@ 'taket'",
            org_id,
        )
    matched = {r["chunk_index"] for r in rows}
    assert matched == {0}
