"""Unit tests for active proposal conflict planning."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.services.proposal_conflict_resolution import (
    ProposalConflictRow,
    plan_active_proposal_conflicts,
)


def _row(
    job_id: uuid.UUID,
    status: str,
    *,
    created_at: datetime | None = None,
    proposal_id: uuid.UUID | None = None,
) -> ProposalConflictRow:
    return ProposalConflictRow(
        id=proposal_id or uuid.uuid4(),
        job_id=job_id,
        status=status,
        created_at=created_at,
    )


def test_no_conflict_single_active_per_job() -> None:
    job = uuid.uuid4()
    plans = plan_active_proposal_conflicts([_row(job, "draft")])
    assert plans == []


def test_keeps_approved_over_draft() -> None:
    job = uuid.uuid4()
    draft = _row(job, "draft", created_at=datetime(2026, 1, 2, tzinfo=UTC))
    approved = _row(job, "approved", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    plans = plan_active_proposal_conflicts([draft, approved])
    assert len(plans) == 1
    assert plans[0].keep_proposal_id == approved.id
    assert draft.id in plans[0].supersede_proposal_ids


def test_newer_wins_within_same_status() -> None:
    job = uuid.uuid4()
    older = _row(job, "pending_approval", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    newer = _row(job, "pending_approval", created_at=datetime(2026, 2, 1, tzinfo=UTC))
    plans = plan_active_proposal_conflicts([older, newer])
    assert plans[0].keep_proposal_id == newer.id


def test_ignores_non_active_status() -> None:
    job = uuid.uuid4()
    plans = plan_active_proposal_conflicts(
        [
            _row(job, "sent"),
            _row(job, "draft"),
        ]
    )
    assert plans == []
