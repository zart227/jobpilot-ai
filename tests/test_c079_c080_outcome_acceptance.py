"""C079/C080: one reward per outcome stage; explicit transition rules."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated *_test database required",
)


@pytest.mark.asyncio
async def test_second_hired_with_different_event_key_does_not_double_reward() -> None:
    from app.db.models import Job, Proposal, Reward
    from app.db.session import AsyncSessionLocal
    from app.services.outcome_service import record_outcome

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c079-{job_id.int}",
                title="C079",
                description="d",
                status="new",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="body",
                status="draft",
            )
        )
        await session.commit()

    for idx in range(2):
        async with AsyncSessionLocal() as session:
            result = await record_outcome(
                session,
                job_id=job_id,
                proposal_id=proposal_id,
                status="hired",
                event_key=f"audit-hired:{job_id}:{idx}",
            )
            await session.commit()
        assert result.duplicate is (idx > 0)

    async with AsyncSessionLocal() as session:
        total = int(
            (
                await session.execute(
                    select(func.sum(Reward.reward_value)).where(Reward.job_id == job_id)
                )
            ).scalar()
            or 0
        )
        assert total == 50


@pytest.mark.asyncio
async def test_replied_to_hired_allowed_hired_to_ignored_blocked() -> None:
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.outcome_service import record_outcome

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c080-{job_id.int}",
                title="C080",
                description="d",
                status="new",
            )
        )
        session.add(
            Proposal(id=proposal_id, job_id=job_id, content="body", status="draft")
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        replied = await record_outcome(
            session, job_id=job_id, proposal_id=proposal_id, status="replied"
        )
        await session.commit()
    assert replied.created is True

    async with AsyncSessionLocal() as session:
        hired = await record_outcome(
            session, job_id=job_id, proposal_id=proposal_id, status="hired"
        )
        await session.commit()
    assert hired.created is True

    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError, match="transition not allowed"):
            await record_outcome(
                session, job_id=job_id, proposal_id=proposal_id, status="ignored"
            )


@pytest.mark.asyncio
async def test_correction_allows_downgrade_with_allow_correction() -> None:
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.outcome_service import record_outcome

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c080-fix-{job_id.int}",
                title="fix",
                description="d",
                status="new",
            )
        )
        session.add(
            Proposal(id=proposal_id, job_id=job_id, content="body", status="draft")
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        await record_outcome(
            session, job_id=job_id, proposal_id=proposal_id, status="hired"
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        corrected = await record_outcome(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="replied",
            event_key=f"correction:{job_id}:replied",
            allow_correction=True,
        )
        await session.commit()
    assert corrected.created is True
