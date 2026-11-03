"""Immutable proposal card payloads (legacy cards require a fresh presentation)."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261015_0005"
down_revision = "20261013_0004"
branch_labels = None
depends_on = None


def upgrade():
    # Historical baseline imports live metadata; support both fresh and existing 0004.
    if not sa.inspect(op.get_bind()).has_table("proposal_approval_snapshots"):
        op.create_table(
            "proposal_approval_snapshots",
            sa.Column("pending_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    op.execute("""CREATE OR REPLACE FUNCTION reject_proposal_snapshot_update() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Proposal approval snapshots are immutable'; END $$""")
    op.execute("""CREATE TRIGGER proposal_snapshot_immutable BEFORE UPDATE ON proposal_approval_snapshots
        FOR EACH ROW EXECUTE FUNCTION reject_proposal_snapshot_update()""")


def downgrade():
    op.drop_table("proposal_approval_snapshots")
    op.execute("DROP FUNCTION reject_proposal_snapshot_update()")
