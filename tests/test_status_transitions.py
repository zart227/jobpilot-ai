"""Status transition guards for processing, approval, and delivery."""

from app.db.send_attempt_states import (
    ATTEMPT_CONFIRMED_SENT,
    ATTEMPT_KNOWN_FAILED,
    ATTEMPT_PENDING,
    ATTEMPT_SENDING,
    ATTEMPT_UNKNOWN_RESULT,
)
from app.db.status_transitions import (
    allows_job_processing_transition,
    allows_proposal_approval_transition,
    allows_send_attempt_transition,
    allows_telegram_pending_transition,
)
from app.db.workflow_states import (
    JOB_STATUS_NEW,
    JOB_STATUS_PENDING_APPROVAL,
    JOB_STATUS_SCORED,
    JOB_STATUS_SENT,
    PROPOSAL_STATUS_APPROVED,
    PROPOSAL_STATUS_DRAFT,
    PROPOSAL_STATUS_PENDING_APPROVAL,
    PROPOSAL_STATUS_SENDING,
    TELEGRAM_PENDING_STATUS_APPROVED,
    TELEGRAM_PENDING_STATUS_PENDING,
)


def test_send_attempt_transition_table() -> None:
    assert allows_send_attempt_transition(ATTEMPT_PENDING, ATTEMPT_SENDING)
    assert allows_send_attempt_transition(ATTEMPT_SENDING, ATTEMPT_UNKNOWN_RESULT)
    assert allows_send_attempt_transition(ATTEMPT_UNKNOWN_RESULT, ATTEMPT_CONFIRMED_SENT)
    assert not allows_send_attempt_transition(ATTEMPT_CONFIRMED_SENT, ATTEMPT_SENDING)
    assert not allows_send_attempt_transition(ATTEMPT_SENDING, ATTEMPT_PENDING)


def test_job_processing_transition_table() -> None:
    assert allows_job_processing_transition(JOB_STATUS_NEW, JOB_STATUS_SCORED)
    assert allows_job_processing_transition(JOB_STATUS_SCORED, JOB_STATUS_PENDING_APPROVAL)
    assert not allows_job_processing_transition(JOB_STATUS_SENT, JOB_STATUS_NEW)
    assert not allows_job_processing_transition(JOB_STATUS_PENDING_APPROVAL, JOB_STATUS_NEW)


def test_proposal_and_telegram_approval_transitions() -> None:
    assert allows_proposal_approval_transition(PROPOSAL_STATUS_DRAFT, PROPOSAL_STATUS_PENDING_APPROVAL)
    assert allows_proposal_approval_transition(PROPOSAL_STATUS_APPROVED, PROPOSAL_STATUS_SENDING)
    assert not allows_proposal_approval_transition(PROPOSAL_STATUS_APPROVED, PROPOSAL_STATUS_DRAFT)
    assert allows_telegram_pending_transition(
        TELEGRAM_PENDING_STATUS_PENDING, TELEGRAM_PENDING_STATUS_APPROVED
    )
    assert not allows_telegram_pending_transition(
        TELEGRAM_PENDING_STATUS_APPROVED, TELEGRAM_PENDING_STATUS_PENDING
    )


def test_known_failed_retry_path() -> None:
    assert allows_send_attempt_transition(ATTEMPT_KNOWN_FAILED, ATTEMPT_SENDING)
    assert allows_send_attempt_transition(ATTEMPT_SENDING, ATTEMPT_KNOWN_FAILED)
