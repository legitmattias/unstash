"""Job progress and audit log.

Adds two operational tables that close out Phase B's schema work:

  - ``job_progress`` mirrors Taskiq's task state so the API and UI can
    surface progress to users without depending on Taskiq's internal
    storage.
  - ``audit_log`` is the append-only record of org-scoped actions
    (uploads, member changes, role updates, connector lifecycle, etc.).
    Pre-org events such as signup are not logged here.

``audit_log.org_id`` is NOT NULL — see the M2 Phase B plan for the
rationale.

Revision ID: 0005_jobs_and_audit
Revises: 0004_search_logs
Create Date: 2026-06-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# Alembic identifiers.
revision: str = "0005_jobs_and_audit"
down_revision: str | None = "0004_search_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the job_progress and audit_log tables."""
    op.create_table(
        "job_progress",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("task_name", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("progress_percent", sa.Integer(), nullable=True),
        sa.Column("progress_detail", JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_progress")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_job_progress_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("task_id", name=op.f("uq_job_progress_task_id")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_job_progress_status_valid"),
        ),
        sa.CheckConstraint(
            "progress_percent IS NULL OR (progress_percent BETWEEN 0 AND 100)",
            name=op.f("ck_job_progress_progress_percent_range"),
        ),
    )
    op.create_index(
        "ix_job_progress_org_id_status",
        "job_progress",
        ["org_id", "status"],
    )

    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column(
            "metadata",
            JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            name=op.f("fk_audit_log_org_id_organisations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_log_actor_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_audit_log_org_id_created_at",
        "audit_log",
        ["org_id", "created_at"],
    )
    op.create_index(
        "ix_audit_log_actor_user_id_created_at",
        "audit_log",
        ["actor_user_id", "created_at"],
    )


def downgrade() -> None:
    """Drop the audit_log and job_progress tables."""
    op.drop_index("ix_audit_log_actor_user_id_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_org_id_created_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_job_progress_org_id_status", table_name="job_progress")
    op.drop_table("job_progress")
