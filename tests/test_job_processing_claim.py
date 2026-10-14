"""Unit tests for job processing lease helpers (no DB)."""

import uuid
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.db.models import Job
from app.services.job_processing import has_active_processing_claim


def test_has_active_processing_claim_false_without_token() -> None:
    job = Job(
        platform="kwork",
        external_id="x",
        title="t",
        description="d",
    )
    assert not has_active_processing_claim(job, Settings(_env_file=None))


def test_has_active_processing_claim_true_inside_lease() -> None:
    settings = Settings(_env_file=None, JOB_PROCESSING_LEASE_SECONDS=300)
    job = Job(
        platform="kwork",
        external_id="x",
        title="t",
        description="d",
        processing_claim_token=uuid.uuid4(),
        processing_claimed_at=datetime.now(UTC),
    )
    assert has_active_processing_claim(job, settings)


def test_has_active_processing_claim_false_after_lease_expired() -> None:
    settings = Settings(_env_file=None, JOB_PROCESSING_LEASE_SECONDS=60)
    job = Job(
        platform="kwork",
        external_id="x",
        title="t",
        description="d",
        processing_claim_token=uuid.uuid4(),
        processing_claimed_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    assert not has_active_processing_claim(job, settings)
