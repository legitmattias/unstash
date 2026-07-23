"""Clustering tables and document assignment.

Adds ``clustering_runs`` (one row per clustering execution: parameters,
counts, quality signals) and ``clusters`` (one row per discovered category
within a run), plus a nullable ``documents.cluster_id`` pointing at the
latest run's assignment. NULL ``cluster_id`` means not yet clustered or
marked as noise.

Both new tables are tenant-scoped: they carry ``org_id`` and get the same
``tenant_isolation`` policy as the tables covered in 0006 (new tables
enable their own RLS).

Revision ID: 0017_clustering
Revises: 0016_own_membership_policy
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# Alembic identifiers.
revision: str = "0017_clustering"
down_revision: str | None = "0016_own_membership_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_PREDICATE = "org_id = current_setting('app.current_org_id')::uuid"
_POLICY_NAME = "tenant_isolation"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_POLICY_NAME} ON {table}
            FOR ALL
            TO unstash_app
            USING ({_POLICY_PREDICATE})
            WITH CHECK ({_POLICY_PREDICATE});
        """
    )


def upgrade() -> None:
    """Create clustering_runs and clusters, and link documents to clusters."""
    op.create_table(
        "clustering_runs",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("cluster_count", sa.Integer(), nullable=True),
        sa.Column("noise_count", sa.Integer(), nullable=True),
        sa.Column("silhouette", sa.Float(), nullable=True),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clustering_runs")),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_clustering_runs_clustering_run_status_valid"),
        ),
        sa.CheckConstraint(
            "triggered_by IN ('threshold', 'doubling', 'manual')",
            name=op.f("ck_clustering_runs_clustering_run_trigger_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_clustering_runs_org_id_organisations"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_clustering_runs_org_id_created_at",
        "clustering_runs",
        ["org_id", "created_at"],
    )

    op.create_table(
        "clusters",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "label_source",
            sa.Text(),
            server_default=sa.text("'keyword_fallback'"),
            nullable=False,
        ),
        sa.Column("keywords", JSONB(), nullable=False),
        sa.Column("representative_document_ids", JSONB(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clusters")),
        sa.CheckConstraint(
            "label_source IN ('keyword_fallback', 'llm', 'user_edited')",
            name=op.f("ck_clusters_cluster_label_source_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_clusters_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["clustering_runs.id"],
            name=op.f("fk_clusters_run_id_clustering_runs"),
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_clusters_org_id_run_id", "clusters", ["org_id", "run_id"])

    op.add_column("documents", sa.Column("cluster_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_documents_cluster_id_clusters"),
        "documents",
        "clusters",
        ["cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_org_id_cluster_id",
        "documents",
        ["org_id", "cluster_id"],
    )

    _enable_rls("clustering_runs")
    _enable_rls("clusters")


def downgrade() -> None:
    """Unlink documents and drop the clustering tables."""
    op.drop_index("ix_documents_org_id_cluster_id", "documents")
    op.drop_constraint(op.f("fk_documents_cluster_id_clusters"), "documents")
    op.drop_column("documents", "cluster_id")

    for table in ("clusters", "clustering_runs"):
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table};")

    op.drop_index("ix_clusters_org_id_run_id", "clusters")
    op.drop_table("clusters")
    op.drop_index("ix_clustering_runs_org_id_created_at", "clustering_runs")
    op.drop_table("clustering_runs")
