"""Enforce at most one active scoring_weights row."""

import sqlalchemy as sa
from alembic import op

revision = "20261103_0010"
down_revision = "20261103_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("scoring_weights"):
        return

    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           ORDER BY is_active DESC, created_at DESC
                       ) AS rn
                FROM scoring_weights
                WHERE is_active IS TRUE
            )
            UPDATE scoring_weights sw
            SET is_active = FALSE
            FROM ranked r
            WHERE sw.id = r.id AND r.rn > 1
            """
        )
    )

    insp = sa.inspect(bind)
    indexes = {idx["name"] for idx in insp.get_indexes("scoring_weights")}
    if "uq_scoring_weights_one_active" not in indexes:
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_scoring_weights_one_active "
                "ON scoring_weights (is_active) WHERE is_active IS TRUE"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("scoring_weights"):
        return
    insp = sa.inspect(bind)
    indexes = {idx["name"] for idx in insp.get_indexes("scoring_weights")}
    if "uq_scoring_weights_one_active" in indexes:
        op.drop_index("uq_scoring_weights_one_active", table_name="scoring_weights")
