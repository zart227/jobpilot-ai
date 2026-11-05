"""Explicit transition guards for processing, approval, and delivery domains."""

from __future__ import annotations

from app.db.send_attempt_states import (
    ATTEMPT_CANCELLED,
    ATTEMPT_CONFIRMED_SENT,
    ATTEMPT_KNOWN_FAILED,
    ATTEMPT_NEEDS_ATTENTION,
    ATTEMPT_PENDING,
    ATTEMPT_SENDING,
    ATTEMPT_UNKNOWN_RESULT,
)
from app.db.workflow_states import (
    JOB_STATUS_APPROVED,
    JOB_STATUS_FILTERED,
    JOB_STATUS_NEW,
    JOB_STATUS_PENDING_APPROVAL,
    JOB_STATUS_PERMANENT_ERROR,
    JOB_STATUS_RETRYABLE_ERROR,
    JOB_STATUS_SCORED,
    JOB_STATUS_SENDING,
    JOB_STATUS_SENT,
    JOB_STATUS_SIMULATED_SENT,
    PROPOSAL_STATUS_APPROVED,
    PROPOSAL_STATUS_DRAFT,
    PROPOSAL_STATUS_EDITED,
    PROPOSAL_STATUS_PENDING_APPROVAL,
    PROPOSAL_STATUS_SENDING,
    PROPOSAL_STATUS_SENT,
    PROPOSAL_STATUS_SIMULATED_SENT,
    PROPOSAL_STATUS_SKIPPED,
    PROPOSAL_STATUS_SUPERSEDED,
    TELEGRAM_PENDING_STATUS_APPROVED,
    TELEGRAM_PENDING_STATUS_EDITED,
    TELEGRAM_PENDING_STATUS_PENDING,
    TELEGRAM_PENDING_STATUS_SKIPPED,
    TELEGRAM_PENDING_STATUS_SUPERSEDED,
)

_SEND_ATTEMPT_ALLOWED: dict[str, frozenset[str]] = {
    ATTEMPT_PENDING: frozenset({ATTEMPT_SENDING, ATTEMPT_CANCELLED}),
    ATTEMPT_SENDING: frozenset(
        {
            ATTEMPT_CONFIRMED_SENT,
            ATTEMPT_KNOWN_FAILED,
            ATTEMPT_UNKNOWN_RESULT,
            ATTEMPT_NEEDS_ATTENTION,
        }
    ),
    ATTEMPT_KNOWN_FAILED: frozenset(
        {ATTEMPT_SENDING, ATTEMPT_NEEDS_ATTENTION, ATTEMPT_CANCELLED}
    ),
    ATTEMPT_UNKNOWN_RESULT: frozenset({ATTEMPT_CONFIRMED_SENT, ATTEMPT_NEEDS_ATTENTION}),
    ATTEMPT_NEEDS_ATTENTION: frozenset(
        {ATTEMPT_PENDING, ATTEMPT_SENDING, ATTEMPT_CANCELLED}
    ),
    ATTEMPT_CONFIRMED_SENT: frozenset(),
    ATTEMPT_CANCELLED: frozenset(),
}

_JOB_PROCESSING_ALLOWED: dict[str, frozenset[str]] = {
    JOB_STATUS_NEW: frozenset(
        {
            JOB_STATUS_FILTERED,
            JOB_STATUS_SCORED,
            JOB_STATUS_RETRYABLE_ERROR,
            JOB_STATUS_PERMANENT_ERROR,
        }
    ),
    JOB_STATUS_FILTERED: frozenset({JOB_STATUS_RETRYABLE_ERROR, JOB_STATUS_PERMANENT_ERROR}),
    JOB_STATUS_SCORED: frozenset(
        {
            JOB_STATUS_PENDING_APPROVAL,
            JOB_STATUS_FILTERED,
            JOB_STATUS_RETRYABLE_ERROR,
            JOB_STATUS_PERMANENT_ERROR,
        }
    ),
    JOB_STATUS_RETRYABLE_ERROR: frozenset(
        {
            JOB_STATUS_NEW,
            JOB_STATUS_FILTERED,
            JOB_STATUS_SCORED,
            JOB_STATUS_PERMANENT_ERROR,
        }
    ),
    JOB_STATUS_PENDING_APPROVAL: frozenset(
        {JOB_STATUS_APPROVED, JOB_STATUS_SCORED, JOB_STATUS_PERMANENT_ERROR}
    ),
    JOB_STATUS_APPROVED: frozenset({JOB_STATUS_SENDING, JOB_STATUS_SENT, JOB_STATUS_SIMULATED_SENT}),
    JOB_STATUS_SENDING: frozenset({JOB_STATUS_SENT, JOB_STATUS_SIMULATED_SENT}),
}

_PROPOSAL_APPROVAL_ALLOWED: dict[str, frozenset[str]] = {
    PROPOSAL_STATUS_DRAFT: frozenset(
        {PROPOSAL_STATUS_PENDING_APPROVAL, PROPOSAL_STATUS_SKIPPED, PROPOSAL_STATUS_SUPERSEDED}
    ),
    PROPOSAL_STATUS_PENDING_APPROVAL: frozenset(
        {PROPOSAL_STATUS_APPROVED, PROPOSAL_STATUS_EDITED, PROPOSAL_STATUS_SUPERSEDED, PROPOSAL_STATUS_SKIPPED}
    ),
    PROPOSAL_STATUS_APPROVED: frozenset(
        {PROPOSAL_STATUS_SENDING, PROPOSAL_STATUS_SENT, PROPOSAL_STATUS_SIMULATED_SENT, PROPOSAL_STATUS_EDITED}
    ),
    PROPOSAL_STATUS_EDITED: frozenset({PROPOSAL_STATUS_PENDING_APPROVAL, PROPOSAL_STATUS_APPROVED}),
    PROPOSAL_STATUS_SENDING: frozenset({PROPOSAL_STATUS_SENT, PROPOSAL_STATUS_SIMULATED_SENT}),
}

_TELEGRAM_PENDING_ALLOWED: dict[str, frozenset[str]] = {
    TELEGRAM_PENDING_STATUS_PENDING: frozenset(
        {
            TELEGRAM_PENDING_STATUS_APPROVED,
            TELEGRAM_PENDING_STATUS_EDITED,
            TELEGRAM_PENDING_STATUS_SKIPPED,
            TELEGRAM_PENDING_STATUS_SUPERSEDED,
        }
    ),
    TELEGRAM_PENDING_STATUS_APPROVED: frozenset(
        {TELEGRAM_PENDING_STATUS_EDITED, "sent", "simulated_sent", TELEGRAM_PENDING_STATUS_SUPERSEDED}
    ),
    TELEGRAM_PENDING_STATUS_EDITED: frozenset(
        {TELEGRAM_PENDING_STATUS_APPROVED, "sent", "simulated_sent", TELEGRAM_PENDING_STATUS_SUPERSEDED}
    ),
}


def allows_send_attempt_transition(previous: str | None, new_status: str) -> bool:
    if previous is None:
        return new_status == ATTEMPT_PENDING
    return new_status in _SEND_ATTEMPT_ALLOWED.get(previous, frozenset())


def allows_job_processing_transition(previous: str | None, new_status: str) -> bool:
    if not previous or previous == new_status:
        return True
    allowed = _JOB_PROCESSING_ALLOWED.get(previous)
    if allowed is None:
        return False
    return new_status in allowed


def allows_proposal_approval_transition(previous: str | None, new_status: str) -> bool:
    if not previous:
        return True
    return new_status in _PROPOSAL_APPROVAL_ALLOWED.get(previous, frozenset({new_status}))


def allows_telegram_pending_transition(previous: str | None, new_status: str) -> bool:
    if not previous:
        return True
    return new_status in _TELEGRAM_PENDING_ALLOWED.get(previous, frozenset({new_status}))
