"""Documents and chunks: the core ingested-content schema.

Adds the ``documents`` and ``chunks`` tables plus the two hybrid-search
indexes that have no SQLAlchemy expression and are therefore written as raw
SQL here:

  - a pgvectorscale StreamingDiskANN index on ``chunks.embedding`` (cosine)
  - a ParadeDB pg_search BM25 index on ``chunks.text`` (ICU tokenizer) with
    ``org_id`` included for tenant filter pushdown

Revision ID: 0002_documents
Revises: 0001_initial_auth
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# Alembic identifiers.
revision: str = "0002_documents"
down_revision: str | None = "0001_initial_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 2048


def upgrade() -> None:
    """Create the documents and chunks tables and their indexes."""
    op.create_table(
        "documents",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        # FK to connectors is added in migration 0003.
        sa.Column("connector_id", sa.Uuid(), nullable=True),
        sa.Column("connector_resource_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source_uri", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("parsing_error", sa.String(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_documents_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'parsing', 'parsed', 'indexed', 'failed')",
            name=op.f("ck_documents_status_valid"),
        ),
    )
    op.create_index(
        "ix_documents_org_id_created_at",
        "documents",
        ["org_id", "created_at"],
    )
    op.create_index(
        "ix_documents_org_id_status",
        "documents",
        ["org_id", "status"],
    )
    op.create_index(
        "ix_documents_org_id_content_hash",
        "documents",
        ["org_id", "content_hash"],
    )
    op.create_index(
        "uq_documents_connector_resource",
        "documents",
        ["org_id", "connector_id", "connector_resource_id"],
        unique=True,
        postgresql_where=sa.text("connector_id IS NOT NULL"),
    )

    op.create_table(
        "chunks",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("char_offset_start", sa.Integer(), nullable=False),
        sa.Column("char_offset_end", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_chunks_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name=op.f("uq_chunks_document_id"),
        ),
    )
    op.create_index(
        "ix_chunks_org_id_document_id_chunk_index",
        "chunks",
        ["org_id", "document_id", "chunk_index"],
    )

    # Specialised indexes — raw SQL, kept out of the model metadata and ignored
    # by autogenerate (see alembic/env.py _UNMANAGED_INDEXES).
    #
    # pgvectorscale StreamingDiskANN, cosine distance. The full 2048-dim vector
    # is indexed; a leading subset can be indexed later via WITH
    # (num_dimensions = N) if the M3 re-evaluation favours a smaller index.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_diskann "
        "ON chunks USING diskann (embedding vector_cosine_ops)"
    )
    # ParadeDB pg_search BM25. key_field must be the first column and unique.
    # The ICU tokenizer segments multilingual text (Swedish + English) without
    # language-specific stemming; org_id is indexed for tenant filter pushdown.
    op.execute(
        "CREATE INDEX ix_chunks_text_bm25 ON chunks "
        "USING bm25 (id, (text::pdb.icu), org_id) "
        "WITH (key_field='id')"
    )


def downgrade() -> None:
    """Drop the chunks and documents tables and their indexes."""
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_bm25")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_diskann")
    op.drop_index("ix_chunks_org_id_document_id_chunk_index", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("uq_documents_connector_resource", table_name="documents")
    op.drop_index("ix_documents_org_id_content_hash", table_name="documents")
    op.drop_index("ix_documents_org_id_status", table_name="documents")
    op.drop_index("ix_documents_org_id_created_at", table_name="documents")
    op.drop_table("documents")
