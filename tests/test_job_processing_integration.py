"""Job processing claim lease on PostgreSQL."""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)


@pytest.mark.asyncio
async def test_try_claim_release_and_stale_recovery() -> None:
    from app.config import Settings, get_settings
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.job_processing import (
        has_active_processing_claim,
        recover_stale_job_claims,
        release_job_claim,
        try_claim_job,
    )

    get_settings.cache_clear()
    settings = Settings(_env_file=None, JOB_PROCESSING_LEASE_SECONDS=60)

    job_id = uuid.uuid4()
    external_id = f"kwork-claim-{job_id.hex[:12]}"
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="Claim test",
                description="d",
                status="scored",
            )
        )
        await session.commit()

    claim = await try_claim_job(job_id, owner="integration-test")
    assert claim is not None
    assert claim.job_id == job_id

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert has_active_processing_claim(job, settings)

    blocked = await try_claim_job(job_id, owner="other")
    assert blocked is None

    await release_job_claim(job_id, claim.claim_token, stage="done")

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert not has_active_processing_claim(job, settings)

    claim2 = await try_claim_job(job_id, owner="integration-test-2")
    assert claim2 is not None

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        from datetime import UTC, datetime, timedelta

        job.processing_claimed_at = datetime.now(UTC) - timedelta(seconds=120)
        await session.commit()

    recovered = await recover_stale_job_claims(settings)
    assert recovered >= 1

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.processing_claim_token is None
