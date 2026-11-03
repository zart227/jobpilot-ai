"""Acceptance-style flows without external platform or paid LLM calls."""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)


@pytest.fixture
def platform_dry_run(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_DRY_RUN", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_proposal_send_dry_run_records_confirmed_sent(platform_dry_run) -> None:
    from sqlalchemy import select

    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.proposal_sender import ProposalSender

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    external_id = f"kwork-dry-{job_id.hex[:12]}"
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="Dry run job",
                description="Test",
                url="https://kwork.ru/projects/1",
                status="approved",
                budget_min=1000,
                budget_max=2000,
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Тестовый отклик для dry-run.",
                status="approved",
                version=1,
            )
        )
        await session.commit()

    from conftest import seed_test_kwork_eligibility, seed_test_proposal_approval

    async with AsyncSessionLocal() as session:
        await seed_test_kwork_eligibility(session, job_id)
        await seed_test_proposal_approval(session, job_id, proposal_id)

    sender = ProposalSender()
    ok, error, debug = await sender.send(
        str(job_id), str(proposal_id), "Тестовый отклик для dry-run."
    )
    assert ok is True
    assert error is None
    assert debug and debug.get("dry_run") is True

    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, proposal_id)
        assert proposal is not None
        assert proposal.status == "sent"
        result = await session.execute(
            select(SendAttempt).where(
                SendAttempt.proposal_id == proposal_id,
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            )
        )
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_hired_outcome_idempotent(platform_dry_run) -> None:
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.outcome_service import record_outcome

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    external_id = f"kwork-outcome-{job_id.hex[:12]}"
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=external_id,
                title="Outcome job",
                description="Test",
                status="sent",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="Sent text",
                status="sent",
                version=1,
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        first = await record_outcome(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="hired",
        )
        await session.commit()
        second = await record_outcome(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="hired",
        )

    assert first.created is True
    assert first.reward == 50
    assert second.duplicate is True
    assert second.reward == 50
