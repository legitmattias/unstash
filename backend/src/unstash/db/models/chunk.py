"""Chunk model — a retrievable span of a document with its embedding.

Documents are split into ordered chunks. Each chunk carries the text span,
its character offsets back into the source, a token count for budgeting, and
a dense embedding for vector search.

``org_id`` is denormalised from the parent document so Row-Level Security
predicates and the hybrid-search indexes can filter on a local column rather
than joining to ``documents``.

Two specialised indexes on this table are created in migration 0002 via raw
SQL rather than declared here, because their syntax has no SQLAlchemy
expression:

  - a pgvectorscale StreamingDiskANN index on ``embedding`` (cosine)
  - a ParadeDB pg_search BM25 index on ``text`` (ICU tokenizer) plus
    ``org_id`` for tenant filter pushdown

The Alembic environment is configured to ignore these during autogenerate so
they are not spuriously dropped.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from unstash.db.models.base import Base

if TYPE_CHECKING:
    from unstash.db.models.document import Document

# Jina v4 native embedding dimension. Matryoshka means a leading subset can be
# indexed later (pgvectorscale `num_dimensions`) without re-embedding; the
# full vector is stored regardless. Revisited at M3 against real data.
EMBEDDING_DIM = 2048


class Chunk(Base):
    """A retrievable text span of a document, with its embedding.

    No ``updated_at`` — chunks are immutable once written. Re-chunking a
    document deletes and recreates its chunks rather than mutating them.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        # Primary lookup path; leads with org_id to match the RLS predicate.
        Index("ix_chunks_org_id_document_id_chunk_index", "org_id", "document_id", "chunk_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_offset_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_offset_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nullable because parsing (M3-B) writes chunks before embedding (M3-C).
    # The DiskANN index naturally skips NULL rows, so search results never
    # contain a chunk that hasn't been embedded yet.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    document: Mapped[Document] = relationship(back_populates="chunks")
