"""Durable queue for deferred Qdrant memory indexing."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261102_0008"
down_revision = "20261015_0007"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("memory_index_pending"):
        return
    op.create_table(
        "memory_index_pending",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("dedupe_key", sa.String(512), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_memory_index_pending_dedupe_key"),
    )
    op.create_index("ix_memory_index_pending_created_at", "memory_index_pending", ["created_at"])


def downgrade():
    op.drop_table("memory_index_pending")
