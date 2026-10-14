"""Outcome/reward idempotency keys.

Revision ID: 20261013_0004
Revises: 20261013_0003
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "20261013_0004"
down_revision = "20261013_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("outcomes"):
        cols = {c["name"] for c in inspector.get_columns("outcomes")}
        if "event_key" not in cols:
            op.add_column("outcomes", sa.Column("event_key", sa.String(255), nullable=True))
            op.execute("UPDATE outcomes SET event_key = 'legacy-' || id::text WHERE event_key IS NULL")
            op.alter_column("outcomes", "event_key", nullable=False)
            op.create_unique_constraint("uq_outcomes_event_key", "outcomes", ["event_key"])

    if inspector.has_table("rewards"):
        cols = {c["name"] for c in inspector.get_columns("rewards")}
        if "event_key" not in cols:
            op.add_column("rewards", sa.Column("event_key", sa.String(255), nullable=True))
            op.execute("UPDATE rewards SET event_key = 'legacy-' || id::text WHERE event_key IS NULL")
            op.alter_column("rewards", "event_key", nullable=False)
            op.create_unique_constraint("uq_rewards_event_key", "rewards", ["event_key"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("outcomes"):
        cols = {c["name"] for c in inspector.get_columns("outcomes")}
        if "event_key" in cols:
            op.drop_constraint("uq_outcomes_event_key", "outcomes", type_="unique")
            op.drop_column("outcomes", "event_key")
    if inspector.has_table("rewards"):
        cols = {c["name"] for c in inspector.get_columns("rewards")}
        if "event_key" in cols:
            op.drop_constraint("uq_rewards_event_key", "rewards", type_="unique")
            op.drop_column("rewards", "event_key")
