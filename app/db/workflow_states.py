"""Canonical job/proposal/approval statuses for pipeline and delivery."""

from __future__ import annotations

# Job lifecycle (pipeline + delivery)
JOB_STATUS_NEW = "new"
JOB_STATUS_FILTERED = "filtered"
JOB_STATUS_SCORED = "scored"
JOB_STATUS_PENDING_APPROVAL = "pending_approval"
JOB_STATUS_APPROVED = "approved"
JOB_STATUS_SENDING = "sending"
JOB_STATUS_SENT = "sent"
JOB_STATUS_SIMULATED_SENT = "simulated_sent"
JOB_STATUS_RETRYABLE_ERROR = "retryable_error"
JOB_STATUS_PERMANENT_ERROR = "permanent_error"

JOB_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        JOB_STATUS_SENT,
        JOB_STATUS_SIMULATED_SENT,
        JOB_STATUS_FILTERED,
        JOB_STATUS_PERMANENT_ERROR,
    }
)

JOB_PROTECTED_FROM_PIPELINE_OVERWRITE: frozenset[str] = frozenset(
    {
        JOB_STATUS_PENDING_APPROVAL,
        JOB_STATUS_APPROVED,
        JOB_STATUS_SENDING,
        JOB_STATUS_SENT,
        JOB_STATUS_SIMULATED_SENT,
    }
)

# Proposal lifecycle
PROPOSAL_STATUS_DRAFT = "draft"
PROPOSAL_STATUS_PENDING_APPROVAL = "pending_approval"
PROPOSAL_STATUS_APPROVED = "approved"
PROPOSAL_STATUS_EDITED = "edited"
PROPOSAL_STATUS_SENDING = "sending"
PROPOSAL_STATUS_SENT = "sent"
PROPOSAL_STATUS_SIMULATED_SENT = "simulated_sent"
PROPOSAL_STATUS_SKIPPED = "skipped"
PROPOSAL_STATUS_SUPERSEDED = "superseded"

PROPOSAL_ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        PROPOSAL_STATUS_DRAFT,
        PROPOSAL_STATUS_PENDING_APPROVAL,
        PROPOSAL_STATUS_APPROVED,
        PROPOSAL_STATUS_EDITED,
        PROPOSAL_STATUS_SENDING,
    }
)

TELEGRAM_PENDING_STATUS_PENDING = "pending"
TELEGRAM_PENDING_STATUS_APPROVED = "approved"
TELEGRAM_PENDING_STATUS_SKIPPED = "skipped"
TELEGRAM_PENDING_STATUS_EDITED = "edited"
TELEGRAM_PENDING_STATUS_SUPERSEDED = "superseded"


def is_terminal_job_status(status: str | None) -> bool:
    return status in JOB_TERMINAL_STATUSES


def job_allows_pipeline_persist(status: str | None) -> bool:
    if not status:
        return True
    return status not in JOB_PROTECTED_FROM_PIPELINE_OVERWRITE and status not in JOB_TERMINAL_STATUSES


PROPOSAL_SUBMITTED_STATUSES: frozenset[str] = frozenset(
    {PROPOSAL_STATUS_SENT, PROPOSAL_STATUS_SIMULATED_SENT}
)

JOB_SUBMITTED_STATUSES: frozenset[str] = frozenset(
    {JOB_STATUS_SENT, JOB_STATUS_SIMULATED_SENT}
)


def proposal_is_submitted(status: str | None) -> bool:
    return status in PROPOSAL_SUBMITTED_STATUSES


def job_is_submitted(status: str | None) -> bool:
    return status in JOB_SUBMITTED_STATUSES
