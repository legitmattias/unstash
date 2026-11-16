"""Migration roundtrip and schema-cleanliness tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config

if TYPE_CHECKING:
    from pathlib import Path

    import asyncpg

    from unstash.config import Settings


def _alembic_config(backend_root: Path) -> Config:
    return Config(str(backend_root / "alembic.ini"))


async def test_downgrade_to_base_leaves_only_alembic_version(
    migrations_pool: asyncpg.Pool,
    settings_for_container: Settings,
    backend_root: Path,
) -> None:
    """After downgrade base, the public schema holds only alembic_version."""
    _ = settings_for_container
    config = _alembic_config(backend_root)

    command.downgrade(config, "base")

    try:
        async with migrations_pool.acquire() as conn:
            tables = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
            table_names = [row["tablename"] for row in tables]
    finally:
        command.upgrade(config, "head")

    assert table_names == ["alembic_version"], (
        f"unexpected tables after downgrade base: {table_names}"
    )


async def test_upgrade_head_produces_expected_table_set(
    migrations_pool: asyncpg.Pool,
) -> None:
    """After the fixture's upgrade head, exactly the expected tables exist."""
    expected = {
        "access_tokens",
        "alembic_version",
        "api_tokens",
        "audit_log",
        "chunks",
        "clustering_runs",
        "clusters",
        "connectors",
        "document_locations",
        "document_metadata",
        "documents",
        "job_progress",
        "llm_calls",
        "org_memberships",
        "organisations",
        "search_logs",
        "users",
    }

    async with migrations_pool.acquire() as conn:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        actual = {row["tablename"] for row in rows}

    assert actual == expected


async def test_extensions_remain_after_roundtrip(
    migrations_pool: asyncpg.Pool,
    settings_for_container: Settings,
    backend_root: Path,
) -> None:
    """Extensions installed by init-db.sh survive a downgrade/upgrade roundtrip.

    Migrations do not own the extensions; init-db.sh installed them at first
    boot. Downgrade should leave them alone.
    """
    _ = settings_for_container
    config = _alembic_config(backend_root)

    command.downgrade(config, "base")

    try:
        async with migrations_pool.acquire() as conn:
            rows = await conn.fetch("SELECT extname FROM pg_extension")
            present = {row["extname"] for row in rows}
    finally:
        command.upgrade(config, "head")

    for required in ("citext", "vector", "vectorscale", "pg_search"):
        assert required in present, (
            f"extension dropped during downgrade (should not happen): {required}"
        )


async def test_embedding_width_migration_discards_vectors_and_resets_status(
    migrations_pool: asyncpg.Pool,
    settings_for_container: Settings,
    backend_root: Path,
) -> None:
    """0020 narrows the column, empties it, and returns indexed documents to parsed."""
    _ = settings_for_container
    config = _alembic_config(backend_root)
    old_vector = "[" + ",".join(["0.5"] * 2048) + "]"

    command.downgrade(config, "0019_document_locations")
    try:
        async with migrations_pool.acquire() as conn:
            org_id = await conn.fetchval(
                "INSERT INTO organisations (slug, name) VALUES ('width', 'Width') RETURNING id",
            )
            with_chunks, without_chunks = [
                await conn.fetchval(
                    "INSERT INTO documents "
                    "(org_id, title, source_uri, mime_type, size_bytes, content_hash, "
                    "status, indexed_at) "
                    "VALUES ($1, $2, $2, 'text/plain', 1, $2, 'indexed', now()) RETURNING id",
                    org_id,
                    title,
                )
                for title in ("with-chunks", "without-chunks")
            ]
            await conn.execute(
                "INSERT INTO chunks (org_id, document_id, chunk_index, text, token_count, "
                "char_offset_start, char_offset_end, embedding) "
                "VALUES ($1, $2, 0, 'text', 1, 0, 4, $3::vector)",
                org_id,
                with_chunks,
                old_vector,
            )

        command.upgrade(config, "head")

        async with migrations_pool.acquire() as conn:
            width = await conn.fetchval(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'",
            )
            embedded = await conn.fetchval(
                "SELECT count(*) FROM chunks WHERE embedding IS NOT NULL",
            )
            statuses = {
                row["id"]: (row["status"], row["indexed_at"])
                for row in await conn.fetch(
                    "SELECT id, status, indexed_at FROM documents WHERE org_id = $1",
                    org_id,
                )
            }
            index_present = await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_chunks_embedding_diskann'",
            )
    finally:
        command.upgrade(config, "head")
        async with migrations_pool.acquire() as conn:
            await conn.execute("DELETE FROM organisations WHERE slug = 'width'")

    assert width == 1024
    assert embedded == 0
    assert statuses[with_chunks] == ("parsed", None)
    assert statuses[without_chunks][0] == "indexed"
    assert index_present == 1
