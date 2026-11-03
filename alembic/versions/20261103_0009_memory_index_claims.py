"""Add claim columns to memory_index_pending for worker fencing."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261103_0009"
down_revision = "20261102_0008"
branch_labels = None
depends_on = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table(table):
        return set()
    return {column["name"] for column in insp.get_columns(table)}


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("memory_index_pending"):
        return
    existing = _column_names("memory_index_pending")
    if "claim_token" not in existing:
        op.add_column(
            "memory_index_pending",
            sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        )
    if "claim_expires_at" not in existing:
        op.add_column(
            "memory_index_pending",
            sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "claim_owner" not in existing:
        op.add_column(
            "memory_index_pending",
            sa.Column("claim_owner", sa.String(64), nullable=True),
        )


def downgrade():
    if not sa.inspect(op.get_bind()).has_table("memory_index_pending"):
        return
    existing = _column_names("memory_index_pending")
    if "claim_owner" in existing:
        op.drop_column("memory_index_pending", "claim_owner")
    if "claim_expires_at" in existing:
        op.drop_column("memory_index_pending", "claim_expires_at")
    if "claim_token" in existing:
        op.drop_column("memory_index_pending", "claim_token")
