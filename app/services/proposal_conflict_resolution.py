"""Pick which proposal to keep when multiple rows are in active statuses for one job."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.db.workflow_states import PROPOSAL_ACTIVE_STATUSES

# Lower sort key = higher priority (keep). Matches inbox reply preference, extended for pipeline states.
_ACTIVE_PROPOSAL_KEEP_PRIORITY: dict[str, int] = {
    "sending": 0,
    "approved": 1,
    "edited": 2,
    "pending_approval": 3,
    "draft": 4,
}


@dataclass(frozen=True)
class ProposalConflictRow:
    id: uuid.UUID
    job_id: uuid.UUID
    status: str
    created_at: Any | None

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> ProposalConflictRow:
        raw_id = row["id"]
        raw_job = row["job_id"]
        return cls(
            id=raw_id if isinstance(raw_id, uuid.UUID) else uuid.UUID(str(raw_id)),
            job_id=raw_job if isinstance(raw_job, uuid.UUID) else uuid.UUID(str(raw_job)),
            status=str(row["status"]),
            created_at=row.get("created_at"),
        )


@dataclass(frozen=True)
class JobProposalConflictPlan:
    job_id: uuid.UUID
    keep_proposal_id: uuid.UUID
    supersede_proposal_ids: tuple[uuid.UUID, ...]
    proposals: tuple[ProposalConflictRow, ...]


def _sort_key(row: ProposalConflictRow) -> tuple[int, float]:
    priority = _ACTIVE_PROPOSAL_KEEP_PRIORITY.get(row.status, 99)
    ts = 0.0
    if row.created_at is not None:
        ts = row.created_at.timestamp()
    return (priority, -ts)


def plan_active_proposal_conflicts(
    proposals: list[ProposalConflictRow],
) -> list[JobProposalConflictPlan]:
    """Group active proposals by job_id; for jobs with >1 active, pick one keeper."""
    by_job: dict[uuid.UUID, list[ProposalConflictRow]] = {}
    for row in proposals:
        if row.status not in PROPOSAL_ACTIVE_STATUSES:
            continue
        by_job.setdefault(row.job_id, []).append(row)

    plans: list[JobProposalConflictPlan] = []
    for job_id, rows in sorted(by_job.items(), key=lambda item: str(item[0])):
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=_sort_key)
        keeper = ordered[0]
        supersede = tuple(p.id for p in ordered[1:])
        plans.append(
            JobProposalConflictPlan(
                job_id=job_id,
                keep_proposal_id=keeper.id,
                supersede_proposal_ids=supersede,
                proposals=tuple(ordered),
            )
        )
    return plans
