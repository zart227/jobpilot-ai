"""C082: replied/hired learning uses confirmed send snapshot only (no draft fallback)."""

from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)

LEARNING_JSON = json.dumps(
    {
        "weights": {
            "skill_match": 0.25,
            "budget": 0.05,
            "complexity": 0.35,
            "client_quality": 0.25,
            "competition": 0.10,
        },
        "insights": [],
        "top_platforms": ["kwork"],
    }
)


@pytest.fixture
def mock_learning_llm(monkeypatch: pytest.MonkeyPatch):
    from app.agents import learning_agent
    from app.config import Settings
    from app.memory.qdrant_store import get_memory_store

    llm = SimpleNamespace(complete=AsyncMock(return_value=LEARNING_JSON))
    monkeypatch.setattr(learning_agent, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(learning_agent, "get_simple_llm_provider", lambda: llm)
    memory = SimpleNamespace(
        store_successful_proposal=AsyncMock(return_value=False),
        store_pricing_phrase=AsyncMock(return_value=False),
    )

    async def fake_to_thread(func, *args):
        if func is get_memory_store:
            return memory
        return func(*args)

    monkeypatch.setattr(learning_agent.asyncio, "to_thread", fake_to_thread)
    return memory


@pytest.mark.asyncio
async def test_replied_without_confirmed_send_does_not_enqueue_draft(mock_learning_llm):
    from app.db.models import Job, MemoryIndexPending, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.outcome_service import record_outcome_and_learn

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c082-draft-{job_id.hex[:8]}",
                title="t",
                description="d",
                status="sent",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="DRAFT must not enter memory",
                status="approved",
                version=3,
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        result = await record_outcome_and_learn(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="replied",
        )
    assert result.created is True
    mock_learning_llm.store_successful_proposal.assert_not_awaited()

    async with AsyncSessionLocal() as session:
        pending = await session.scalar(
            select(MemoryIndexPending).where(
                MemoryIndexPending.dedupe_key.like(f"{proposal_id}:%")
            )
        )
        assert pending is None


@pytest.mark.asyncio
async def test_replied_uses_confirmed_snapshot_content_and_version(mock_learning_llm):
    from app.db.models import Job, MemoryIndexPending, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.outcome_service import record_outcome_and_learn

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    sent_text = "Confirmed sent body for learning."
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c082-ok-{job_id.hex[:8]}",
                title="t",
                description="d",
                status="sent",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content="different draft",
                status="sent",
                version=1,
            )
        )
        session.add(
            SendAttempt(
                id=uuid.uuid4(),
                kind="proposal",
                idempotency_key=f"c082-{proposal_id}",
                job_id=job_id,
                proposal_id=proposal_id,
                snapshot_content=sent_text,
                snapshot_version=7,
                status=ATTEMPT_CONFIRMED_SENT,
                platform_response={"dry_run": True},
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        await record_outcome_and_learn(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="replied",
        )

    dedupe_key = f"{proposal_id}:replied:7"
    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(MemoryIndexPending).where(MemoryIndexPending.dedupe_key == dedupe_key)
        )
        assert row is not None
        assert row.payload["content"] == sent_text
        assert row.payload["metadata"]["proposal_version"] == 7
