"""Durable human proposal approval events."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261015_0006"
down_revision = "20261015_0005"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("proposal_approval_events"):
        op.create_table(
            "proposal_approval_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("pending_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("message_id", sa.BigInteger(), nullable=False),
            sa.Column("approval_kind", sa.String(32), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_proposal_approval_events_proposal_id", "proposal_approval_events", ["proposal_id"]
        )
    op.execute("""CREATE TRIGGER proposal_approval_event_immutable BEFORE UPDATE ON proposal_approval_events
        FOR EACH ROW EXECUTE FUNCTION reject_proposal_snapshot_update()""")


def downgrade():
    op.drop_table("proposal_approval_events")
