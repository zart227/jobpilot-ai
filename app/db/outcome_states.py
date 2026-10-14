"""Outcome statuses and transition rules."""

from __future__ import annotations

from enum import StrEnum


class OutcomeStatus(StrEnum):
    SENT = "sent"
    IGNORED = "ignored"
    REPLIED = "replied"
    HIRED = "hired"
    SEND_FAILED = "send_failed"


API_OUTCOME_STATUSES: frozenset[str] = frozenset(
    {
        OutcomeStatus.SENT,
        OutcomeStatus.IGNORED,
        OutcomeStatus.REPLIED,
        OutcomeStatus.HIRED,
    }
)

INTERNAL_OUTCOME_STATUSES: frozenset[str] = frozenset({OutcomeStatus.SEND_FAILED})

ALL_OUTCOME_STATUSES: frozenset[str] = API_OUTCOME_STATUSES | INTERNAL_OUTCOME_STATUSES

# Latest outcome per job may move forward along this order; corrections use a new event_key.
_OUTCOME_RANK: dict[str, int] = {
    OutcomeStatus.IGNORED: 0,
    OutcomeStatus.SEND_FAILED: 1,
    OutcomeStatus.SENT: 2,
    OutcomeStatus.REPLIED: 3,
    OutcomeStatus.HIRED: 4,
}


def validate_outcome_status(status: str, *, allow_internal: bool = False) -> str:
    normalized = status.strip().lower()
    allowed = ALL_OUTCOME_STATUSES if allow_internal else API_OUTCOME_STATUSES
    if normalized not in allowed:
        raise ValueError(f"unsupported outcome status: {status}")
    return normalized


def allows_transition(previous: str | None, new_status: str) -> bool:
    """Soft guard: block obvious downgrades unless there was no prior outcome."""
    new_status = validate_outcome_status(new_status, allow_internal=True)
    if not previous:
        return True
    prev = validate_outcome_status(previous, allow_internal=True)
    return _OUTCOME_RANK.get(new_status, 0) >= _OUTCOME_RANK.get(prev, 0)


def default_event_key(
    job_id: str,
    proposal_id: str | None,
    status: str,
) -> str:
    pid = proposal_id or "none"
    return f"{job_id}:{pid}:{status}"
