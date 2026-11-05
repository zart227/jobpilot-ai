"""Operator override append-only journal."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "20261103_0011"
down_revision = "20261103_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("operator_override_events"):
        op.create_table(
            "operator_override_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("actor_source", sa.String(32), nullable=False),
            sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
            sa.Column("reference_type", sa.String(64), nullable=False),
            sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "detail",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
    indexes = {idx.get("name") for idx in inspector.get_indexes("operator_override_events")}
    if "ix_operator_override_events_reference" not in indexes:
        op.create_index(
            "ix_operator_override_events_reference",
            "operator_override_events",
            ["reference_type", "reference_id", "created_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_operator_override_events_reference", table_name="operator_override_events")
    op.drop_table("operator_override_events")
