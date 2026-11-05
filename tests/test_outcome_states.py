import uuid

import pytest

from app.db.outcome_states import (
    allows_transition,
    default_event_key,
    validate_outcome_status,
)


def test_validate_outcome_status_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        validate_outcome_status("draft")


def test_default_event_key_stable() -> None:
    job_id = str(uuid.uuid4())
    proposal_id = str(uuid.uuid4())
    assert default_event_key(job_id, proposal_id, "hired") == default_event_key(
        job_id, proposal_id, "hired"
    )
    assert default_event_key(job_id, proposal_id, "hired") != default_event_key(
        job_id, proposal_id, "replied"
    )


def test_allows_transition_blocks_obvious_downgrade() -> None:
    assert allows_transition("replied", "hired") is True
    assert allows_transition("hired", "ignored") is False
    assert allows_transition(None, "replied") is True
