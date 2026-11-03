"""No external calls: generation/send races on isolated PostgreSQL."""

import os
import uuid

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


@pytest.fixture(autouse=True)
def isolate_agent_constructor(monkeypatch):
    from app.agents.proposal_agent import ProposalAgent

    # Replacing run alone still constructs live Qdrant/LLM clients.
    monkeypatch.setattr(ProposalAgent, "__init__", lambda self: None)


async def seed():
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal

    jid, pid = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=jid,
                platform="fake",
                external_id=str(jid),
                title="Test",
                description="Test",
                is_relevant=True,
                status="pending_approval",
            )
        )
        await session.commit()
        session.add(
            Proposal(id=pid, job_id=jid, content="original", version=1, status="pending_approval")
        )
        await session.commit()
    return jid, pid


async def attempt(jid, pid, status):
    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        session.add(
            SendAttempt(
                kind="proposal",
                job_id=jid,
                proposal_id=pid,
                status=status,
                idempotency_key=str(uuid.uuid4()),
                snapshot_content="original",
                snapshot_version=1,
            )
        )
        await session.commit()


@pytest.mark.parametrize(
    "status", ["sending", "unknown_result", "needs_attention", "confirmed_sent"]
)
async def test_regenerate_refuses_protected_delivery_before_llm(monkeypatch, status):
    from app.agents.proposal_agent import ProposalAgent
    from app.services.job_pipeline import JobPipelineService

    jid, pid = await seed()
    await attempt(jid, pid, status)

    async def forbidden(*args):
        raise AssertionError("LLM must not run")

    monkeypatch.setattr(ProposalAgent, "run", forbidden)
    assert await JobPipelineService().regenerate_proposal(jid) is None


@pytest.mark.parametrize("race", ["sending", "unknown_result", "edit"])
async def test_generation_result_cannot_overwrite_delivery_or_new_revision(monkeypatch, race):
    from app.agents.proposal_agent import ProposalAgent
    from app.db.models import Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.job_pipeline import JobPipelineService

    jid, pid = await seed()

    async def generate(*args):
        if race == "edit":
            async with AsyncSessionLocal() as session:
                row = await session.get(Proposal, pid)
                row.content, row.version = "newer approved text", 2
                await session.commit()
        else:
            await attempt(jid, pid, race)
        return {"proposal_content": "stale generated text"}

    monkeypatch.setattr(ProposalAgent, "run", generate)
    assert await JobPipelineService().regenerate_proposal(jid) is None
    async with AsyncSessionLocal() as session:
        row = await session.get(Proposal, pid)
        assert row.content == ("newer approved text" if race == "edit" else "original")


async def test_safe_regeneration_and_known_failure_still_allowed(monkeypatch):
    from app.agents.proposal_agent import ProposalAgent
    from app.db.models import Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.job_pipeline import JobPipelineService

    jid, pid = await seed()
    await attempt(jid, pid, "known_failed")

    async def generate(*args):
        return {"proposal_content": "new generation"}

    monkeypatch.setattr(ProposalAgent, "run", generate)
    assert await JobPipelineService().regenerate_proposal(jid) == pid
    async with AsyncSessionLocal() as session:
        row = await session.get(Proposal, pid)
        assert (row.content, row.version, row.status) == ("new generation", 2, "pending_approval")


async def test_guard_scope_and_ambiguous_lease(monkeypatch):
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import delivery_blocks_mutation

    jid, pid = await seed()
    await attempt(jid, pid, "sending")
    async with AsyncSessionLocal() as session:
        await session.scalar(select(Job).where(Job.id == jid).with_for_update())
        assert await delivery_blocks_mutation(session, job_id=jid)
        assert not await delivery_blocks_mutation(session, job_id=uuid.uuid4())
        with pytest.raises(ValueError):
            await delivery_blocks_mutation(session)
