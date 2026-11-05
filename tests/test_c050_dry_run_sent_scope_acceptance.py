"""C050: dry-run confirmed sends use simulated_sent, not live sent."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select

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
async def test_dry_run_proposal_uses_simulated_sent_status(platform_dry_run) -> None:
    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.db.workflow_states import (
        JOB_STATUS_SENT,
        JOB_STATUS_SIMULATED_SENT,
        PROPOSAL_STATUS_SENT,
        PROPOSAL_STATUS_SIMULATED_SENT,
    )
    from app.services.delivery_scope import is_live_confirmed_send_attempt
    from app.services.proposal_sender import ProposalSender

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    content = "Dry-run scope test proposal."
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c050-{job_id.hex[:8]}",
                title="C050",
                description="d",
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
                content=content,
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
    ok, error, debug = await sender.send(str(job_id), str(proposal_id), content)
    assert ok is True and error is None
    assert debug and debug.get("dry_run") is True

    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, proposal_id)
        job = await session.get(Job, job_id)
        assert proposal is not None and job is not None
        assert proposal.status == PROPOSAL_STATUS_SIMULATED_SENT
        assert proposal.status != PROPOSAL_STATUS_SENT
        assert job.status == JOB_STATUS_SIMULATED_SENT
        assert job.status != JOB_STATUS_SENT
        attempt = await session.scalar(
            select(SendAttempt).where(
                SendAttempt.proposal_id == proposal_id,
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            )
        )
        assert attempt is not None
        assert (attempt.platform_response or {}).get("dry_run") is True
        assert is_live_confirmed_send_attempt(attempt) is False


@pytest.mark.asyncio
async def test_draft_and_approved_are_not_marked_delivered() -> None:
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.db.workflow_states import PROPOSAL_STATUS_APPROVED, PROPOSAL_STATUS_DRAFT

    job_ids: list[uuid.UUID] = []
    async with AsyncSessionLocal() as session:
        for label, status in (("draft", PROPOSAL_STATUS_DRAFT), ("approved", PROPOSAL_STATUS_APPROVED)):
            job_id = uuid.uuid4()
            job_ids.append(job_id)
            session.add(
                Job(
                    id=job_id,
                    platform="kwork",
                    external_id=f"c050-{label}-{job_id.hex[:8]}",
                    title="t",
                    description="d",
                    status="approved",
                )
            )
            session.add(
                Proposal(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    content=f"{label} body",
                    status=status,
                    version=1,
                )
            )
        await session.commit()

    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(select(Proposal.status).where(Proposal.job_id.in_(job_ids)))
        ).all()
        assert set(rows) <= {PROPOSAL_STATUS_DRAFT, PROPOSAL_STATUS_APPROVED}


@pytest.mark.asyncio
async def test_idempotent_resend_same_proposal_after_simulated_sent(platform_dry_run) -> None:
    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.proposal_sender import ProposalSender

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    content = "C050 idempotent resend body text."
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c050-idem-{job_id.hex[:8]}",
                title="C050 idem",
                description="d",
                url="https://kwork.ru/projects/2",
                status="approved",
                budget_min=1000,
                budget_max=2000,
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content=content,
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
    ok1, _, _ = await sender.send(str(job_id), str(proposal_id), content)
    assert ok1
    ok2, err2, debug2 = await sender.send(str(job_id), str(proposal_id), content)
    assert ok2 and err2 is None
    assert debug2 and debug2.get("offer_action") == "unchanged"

    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count(SendAttempt.id)).where(
                SendAttempt.proposal_id == proposal_id,
                SendAttempt.status == ATTEMPT_CONFIRMED_SENT,
            )
        )
        assert count == 1


async def test_sender_does_not_upgrade_job_already_sent_to_success(monkeypatch):
    from app.services.proposal_sender import ProposalSender
    from app.services.send_attempt_service import ClaimResult

    async def blocked_prepare(*_args, **_kwargs):
        return ClaimResult(action="blocked", reason="job_already_sent"), None, None

    monkeypatch.setattr(
        "app.services.proposal_sender.prepare_proposal_send",
        blocked_prepare,
    )
    sender = ProposalSender()
    ok, error, debug = await sender.send("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002", "body")
    assert ok is False
    assert error == "job_already_sent"
    assert debug == {"error_label": "job_already_sent"}
