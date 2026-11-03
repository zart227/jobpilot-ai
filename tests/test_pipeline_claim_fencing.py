"""Pipeline persistence/recovery must share the job row lock and token."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def seed():
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal

    jid, token = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=jid,
                platform="fake",
                external_id=str(jid),
                title="Fake",
                description="Fake",
                status="new",
                processing_claim_token=token,
                processing_claim_owner="new-worker",
                processing_claimed_at=datetime.now(UTC),
                score=42,
            )
        )
        await session.commit()
    return jid, token


@pytest.mark.parametrize("error", [False, True])
@pytest.mark.parametrize("missing", [False, True])
async def test_stale_or_missing_token_cannot_persist_or_clear_new_claim(error, missing):
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.job_pipeline import JobPipelineService

    jid, token = await seed()
    state = {
        "score": 99,
        "is_relevant": True,
        "proposal_content": "stale text",
        "pipeline_processing_error": error,
    }
    assert (
        await JobPipelineService().persist_pipeline_results(
            jid, state, claim_token=None if missing else uuid.uuid4()
        )
        is None
    )
    async with AsyncSessionLocal() as session:
        row = await session.get(Job, jid)
        assert (row.processing_claim_token, row.status, row.score) == (token, "new", 42)


async def test_current_token_persists_once_and_replay_is_rejected(monkeypatch):
    from app.config import get_settings
    from app.db.models import Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.job_pipeline import JobPipelineService

    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "100")
    get_settings.cache_clear()
    jid, token = await seed()
    state = {"score": 99, "is_relevant": True, "proposal_content": "approved for review"}
    results = await asyncio.gather(
        *[
            JobPipelineService().persist_pipeline_results(jid, state, claim_token=token)
            for _ in range(2)
        ]
    )
    assert sum(result is not None for result in results) == 1
    async with AsyncSessionLocal() as session:
        proposals = (await session.scalars(select(Proposal).where(Proposal.job_id == jid))).all()
        assert len(proposals) == 1


async def test_stale_release_and_error_preserve_current_claim():
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.job_processing import record_processing_error, release_job_claim

    jid, token = await seed()
    await release_job_claim(jid, uuid.uuid4())
    await record_processing_error(jid, uuid.uuid4(), stage="fake", error="fake", retryable=True)
    async with AsyncSessionLocal() as session:
        row = await session.get(Job, jid)
        assert (row.processing_claim_token, row.status) == (token, "new")
    await release_job_claim(jid, token)
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, jid)).processing_claim_token is None


async def test_recovery_skips_locked_row_and_recovers_after_unlock():
    from app.config import get_settings
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.job_processing import recover_stale_job_claims

    jid, _ = await seed()
    async with AsyncSessionLocal() as session:
        row = await session.get(Job, jid)
        row.processing_claimed_at = datetime.now(UTC) - timedelta(
            seconds=get_settings().job_processing_lease_seconds + 60
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(Job).where(Job.id == jid).with_for_update())
        await asyncio.wait_for(recover_stale_job_claims(), timeout=3)
        assert row.processing_claim_token is not None
        await session.rollback()
    await recover_stale_job_claims()
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, jid)).processing_claim_token is None


@pytest.mark.parametrize("status", ["approved", "sent", "pending_approval"])
async def test_current_token_error_cannot_overwrite_protected_status(status):
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.job_processing import record_processing_error

    jid, token = await seed()
    async with AsyncSessionLocal() as session:
        row = await session.get(Job, jid)
        row.status = status
        await session.commit()
    await record_processing_error(jid, token, stage="fake", error="fake", retryable=True)
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, jid)).status == status
