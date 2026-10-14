"""JobPilot AI schema baseline and legacy constraint alignment.

Revision ID: 20261013_0001
Revises:
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.models import Base

revision = "20261013_0001"
down_revision = None
branch_labels = None
depends_on = None

_JOB_PROCESSING_COLUMNS = (
    ("processing_claim_owner", sa.String(64)),
    ("processing_claim_token", postgresql.UUID(as_uuid=True)),
    ("processing_claimed_at", sa.DateTime(timezone=True)),
    ("processing_attempt_count", sa.Integer()),
    ("processing_next_attempt_at", sa.DateTime(timezone=True)),
    ("processing_stage", sa.String(64)),
    ("processing_error_type", sa.String(32)),
)


def _has_constraint(inspector, table: str, name: str) -> bool:
    for item in inspector.get_unique_constraints(table):
        if item.get("name") == name:
            return True
    for item in inspector.get_check_constraints(table):
        if item.get("name") == name:
            return True
    return False


def _has_index(inspector, table: str, name: str) -> bool:
    return any(idx.get("name") == name for idx in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))

    if not inspector.has_table("jobs"):
        Base.metadata.create_all(bind)
        return

    for column_name, column_type in _JOB_PROCESSING_COLUMNS:
        if column_name not in {col["name"] for col in inspector.get_columns("jobs")}:
            server_default = sa.text("0") if column_name == "processing_attempt_count" else None
            op.add_column(
                "jobs",
                sa.Column(column_name, column_type, nullable=True, server_default=server_default),
            )

    if not _has_constraint(inspector, "jobs", "uq_jobs_platform_external_id"):
        op.create_unique_constraint(
            "uq_jobs_platform_external_id",
            "jobs",
            ["platform", "external_id"],
        )

    if not _has_constraint(inspector, "jobs", "ck_jobs_score_range"):
        op.create_check_constraint(
            "ck_jobs_score_range",
            "jobs",
            "score IS NULL OR (score >= 0 AND score <= 100)",
        )

    if not _has_constraint(inspector, "clients", "uq_clients_platform_external_id"):
        op.create_unique_constraint(
            "uq_clients_platform_external_id",
            "clients",
            ["platform", "external_id"],
        )

    if not _has_index(inspector, "jobs", "idx_jobs_processing_next_attempt"):
        op.create_index(
            "idx_jobs_processing_next_attempt",
            "jobs",
            ["processing_next_attempt_at"],
            unique=False,
        )

    if not _has_index(inspector, "proposals", "uq_proposals_one_active_per_job"):
        op.create_index(
            "uq_proposals_one_active_per_job",
            "proposals",
            ["job_id"],
            unique=True,
            postgresql_where=sa.text(
                "status IN ('draft', 'pending_approval', 'approved', 'edited', 'sending')"
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("jobs"):
        return

    if _has_index(inspector, "proposals", "uq_proposals_one_active_per_job"):
        op.drop_index("uq_proposals_one_active_per_job", table_name="proposals")

    if _has_index(inspector, "jobs", "idx_jobs_processing_next_attempt"):
        op.drop_index("idx_jobs_processing_next_attempt", table_name="jobs")

    if _has_constraint(inspector, "clients", "uq_clients_platform_external_id"):
        op.drop_constraint("uq_clients_platform_external_id", "clients", type_="unique")

    if _has_constraint(inspector, "jobs", "ck_jobs_score_range"):
        op.drop_constraint("ck_jobs_score_range", "jobs", type_="check")

    if _has_constraint(inspector, "jobs", "uq_jobs_platform_external_id"):
        op.drop_constraint("uq_jobs_platform_external_id", "jobs", type_="unique")

    for column_name, _ in reversed(_JOB_PROCESSING_COLUMNS):
        if column_name in {col["name"] for col in inspector.get_columns("jobs")}:
            op.drop_column("jobs", column_name)
