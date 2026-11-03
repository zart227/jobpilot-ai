"""Immutable inbox presentation and human approval records."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261015_0007"
down_revision = "20261015_0006"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("inbox_approval_records"):
        op.create_table(
            "inbox_approval_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("pending_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("record_kind", sa.String(32), nullable=False),
            sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("message_id", sa.BigInteger(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_inbox_approval_records_pending_id", "inbox_approval_records", ["pending_id"]
        )
    op.execute("""CREATE TRIGGER inbox_approval_immutable BEFORE UPDATE ON inbox_approval_records
        FOR EACH ROW EXECUTE FUNCTION reject_proposal_snapshot_update()""")


def downgrade():
    op.drop_table("inbox_approval_records")
