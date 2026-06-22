"""API tokens table for Bearer-token authentication.

Each row represents a long-lived bearer credential the operator has
created for a user (and optionally scoped to a specific organisation).

Token storage:
  - ``token_hash`` is the SHA-256 digest (32 bytes) of the full token
    string. Plaintext is shown to the operator exactly once at creation
    time and never persisted.
  - The column is ``UNIQUE``-indexed because verification at auth time
    is a single ``SELECT WHERE token_hash = $1`` and must be O(1).
  - SHA-256 was chosen over Argon2 because the secret portion of the
    token carries ~256 bits of entropy from ``secrets.token_urlsafe(32)``.
    Brute-forcing such a token is infeasible regardless of hash speed;
    slow hashes are for low-entropy human passwords. See ADR 0006 for
    the full rationale and citations (GitHub, GitLab, Stripe, OWASP).

Scoping:
  - ``user_id`` is required: every token belongs to a single user.
  - ``org_id`` is optional. A non-null ``org_id`` scopes the token to a
    specific organisation; null means "any org the user is a member of".
    Either way, the request's org context still comes from the URL slug.

Lifecycle:
  - ``revoked_at`` is set when the operator revokes a token; non-null
    means "reject at auth time".
  - ``expires_at`` is optional; when set, the token is rejected after
    the timestamp passes.
  - ``last_used_at`` is updated best-effort on each successful auth so
    operators can audit which tokens are actively in use.

RLS:
  - Not RLS-protected. Authentication needs to look up tokens before
    the request has any org context, so a tenant-scoped policy would
    not work here. ``users`` and ``access_tokens`` (cookie sessions)
    follow the same pattern and the same reasoning — see migration
    0006_rls_policies and 0007_access_tokens.

Revision ID: 0009_api_tokens
Revises: 0008_admin_role_grants
Create Date: 2026-06-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_api_tokens"
down_revision: str | None = "0008_admin_role_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the api_tokens table."""
    op.create_table(
        "api_tokens",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name=op.f("fk_api_tokens_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organisations.id"],
            ondelete="CASCADE",
            name=op.f("fk_api_tokens_org_id_organisations"),
        ),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_api_tokens_user_id"),
        "api_tokens",
        ["user_id"],
    )


def downgrade() -> None:
    """Drop the api_tokens table."""
    op.drop_index(op.f("ix_api_tokens_user_id"), table_name="api_tokens")
    op.drop_table("api_tokens")
