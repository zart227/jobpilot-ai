"""Send attempts for external delivery.

Revision ID: 20261013_0003
Revises: 20261013_0002
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261013_0003"
down_revision = "20261013_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("send_attempts"):
        return

    op.create_table(
        "send_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("inbox_pending_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("snapshot_content", sa.Text(), nullable=False),
        sa.Column("snapshot_version", sa.Integer()),
        sa.Column("snapshot_metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column("status", sa.String(32), server_default="pending"),
        sa.Column("claim_owner", sa.String(64)),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("platform_response", postgresql.JSONB(), server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["inbox_pending_id"], ["telegram_inbox_pending.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_send_attempts_idempotency"),
    )
    op.create_index("idx_send_attempts_status_next", "send_attempts", ["status", "next_attempt_at"])
    op.create_index("idx_send_attempts_job", "send_attempts", ["job_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("send_attempts"):
        return
    op.drop_index("idx_send_attempts_job", table_name="send_attempts")
    op.drop_index("idx_send_attempts_status_next", table_name="send_attempts")
    op.drop_table("send_attempts")
