"""Inbound messages and notification outbox.

Revision ID: 20261013_0002
Revises: 20261013_0001
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261013_0002"
down_revision = "20261013_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("inbound_messages"):
        op.create_table(
            "inbound_messages",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("platform", sa.String(32), nullable=False, server_default="kwork"),
            sa.Column("message_key", sa.String(255), nullable=False),
            sa.Column("thread_key", sa.String(255), nullable=False),
            sa.Column("kwork_user_id", sa.BigInteger()),
            sa.Column("kwork_username", sa.String(255)),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("raw_metadata", postgresql.JSONB(), server_default="{}"),
            sa.Column("status", sa.String(32), server_default="received"),
            sa.Column("interaction_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("inbox_pending_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "received_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("processed_at", sa.DateTime(timezone=True)),
            sa.Column("notified_at", sa.DateTime(timezone=True)),
            sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["inbox_pending_id"], ["telegram_inbox_pending.id"], ondelete="SET NULL"
            ),
            sa.UniqueConstraint("platform", "message_key", name="uq_inbound_platform_message_key"),
        )
        op.create_index("idx_inbound_status", "inbound_messages", ["status"])

    if not inspector.has_table("notification_outbox"):
        op.create_table(
            "notification_outbox",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("idempotency_key", sa.String(255), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("payload", postgresql.JSONB(), server_default="{}"),
            sa.Column("reference_type", sa.String(64), nullable=False),
            sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("status", sa.String(32), server_default="pending"),
            sa.Column("telegram_message_id", sa.BigInteger()),
            sa.Column("attempt_count", sa.Integer(), server_default="0"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
            sa.Column("claim_owner", sa.String(64)),
            sa.Column("claim_token", postgresql.UUID(as_uuid=True)),
            sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.Text()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("sent_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("idempotency_key", name="uq_notification_outbox_idempotency"),
        )
        op.create_index(
            "idx_notification_outbox_status_next",
            "notification_outbox",
            ["status", "next_attempt_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("notification_outbox"):
        op.drop_index("idx_notification_outbox_status_next", table_name="notification_outbox")
        op.drop_table("notification_outbox")
    if inspector.has_table("inbound_messages"):
        op.drop_index("idx_inbound_status", table_name="inbound_messages")
        op.drop_table("inbound_messages")
