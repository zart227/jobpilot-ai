from app.db.workflow_states import (
    JOB_STATUS_PENDING_APPROVAL,
    JOB_STATUS_SENT,
    JOB_STATUS_SIMULATED_SENT,
    is_terminal_job_status,
    job_allows_pipeline_persist,
    proposal_is_submitted,
)


def test_terminal_job_status() -> None:
    assert is_terminal_job_status(JOB_STATUS_SENT) is True
    assert is_terminal_job_status(JOB_STATUS_SIMULATED_SENT) is True
    assert is_terminal_job_status(JOB_STATUS_PENDING_APPROVAL) is False


def test_job_allows_pipeline_persist() -> None:
    assert job_allows_pipeline_persist("new") is True
    assert job_allows_pipeline_persist(JOB_STATUS_PENDING_APPROVAL) is False
    assert job_allows_pipeline_persist(JOB_STATUS_SENT) is False
    assert job_allows_pipeline_persist(JOB_STATUS_SIMULATED_SENT) is False


def test_proposal_is_submitted() -> None:
    assert proposal_is_submitted("sent") is True
    assert proposal_is_submitted("simulated_sent") is True
    assert proposal_is_submitted("approved") is False
