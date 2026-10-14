import uuid

import pytest

from app.db.outcome_states import (
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
