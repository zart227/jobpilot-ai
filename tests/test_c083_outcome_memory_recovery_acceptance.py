"""C083: Qdrant outage recovers successful-proposal indexing without duplicate rewards."""

from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

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
def mock_learning(monkeypatch: pytest.MonkeyPatch):
    from app.agents import learning_agent
    from app.config import Settings
    from app.memory.qdrant_store import get_memory_store

    llm = SimpleNamespace(complete=AsyncMock(return_value=LEARNING_JSON))
    monkeypatch.setattr(learning_agent, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(learning_agent, "get_simple_llm_provider", lambda: llm)
    agent_persist = AsyncMock()
    monkeypatch.setattr(learning_agent.LearningAgent, "_persist_weights", agent_persist)

    memory = SimpleNamespace(
        store_successful_proposal=AsyncMock(return_value=False),
        store_pricing_phrase=AsyncMock(return_value=False),
    )

    async def fake_to_thread(func, *args):
        if func is get_memory_store:
            return memory
        return func(*args)

    monkeypatch.setattr(learning_agent.asyncio, "to_thread", fake_to_thread)
    return memory, agent_persist


@pytest.mark.asyncio
async def test_c083_qdrant_outage_recovery_without_duplicate_reward(
    mock_learning, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models import Job, MemoryIndexPending, Proposal, Reward, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.memory_index_queue import (
        KIND_SUCCESSFUL_PROPOSAL,
        enqueue_memory_index,
        process_memory_index_queue,
    )
    from app.services.outcome_service import record_outcome

    memory, _persist = mock_learning
    monkeypatch.setattr("app.services.memory_index_queue.get_memory_store", lambda: memory)

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    snapshot = "C083 durable memory after Qdrant recovery"
    dedupe_key = f"{proposal_id}:replied:1"

    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c083-{job_id.hex[:8]}",
                title="C083",
                description="d",
                status="sent",
            )
        )
        session.add(
            Proposal(
                id=proposal_id,
                job_id=job_id,
                content=snapshot,
                status="sent",
                version=1,
            )
        )
        session.add(
            SendAttempt(
                id=uuid.uuid4(),
                kind="proposal",
                idempotency_key=f"c083-{proposal_id}",
                job_id=job_id,
                proposal_id=proposal_id,
                snapshot_content=snapshot,
                snapshot_version=1,
                status=ATTEMPT_CONFIRMED_SENT,
                platform_response={"dry_run": True},
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        created = await record_outcome(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="replied",
        )
        await session.commit()
    assert created.created is True

    from sqlalchemy import delete

    async with AsyncSessionLocal() as session:
        await session.execute(delete(MemoryIndexPending))
        await session.commit()

    assert await enqueue_memory_index(
        KIND_SUCCESSFUL_PROPOSAL,
        dedupe_key,
        {"memory_key": dedupe_key, "content": snapshot, "metadata": {"outcome": "replied"}},
    )

    async with AsyncSessionLocal() as session:
        pending_rows = (
            await session.scalars(
                select(MemoryIndexPending).where(MemoryIndexPending.dedupe_key == dedupe_key)
            )
        ).all()
        assert len(pending_rows) == 1
        reward_count = await session.scalar(
            select(func.count(Reward.id)).where(Reward.job_id == job_id)
        )
        assert int(reward_count or 0) == 1

    memory.store_successful_proposal = AsyncMock(return_value=False)

    first = await process_memory_index_queue(limit=5)
    assert first["memory_queue_failed"] >= 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(
            select(MemoryIndexPending.id).where(MemoryIndexPending.dedupe_key == dedupe_key)
        )

    memory.store_successful_proposal = AsyncMock(return_value=True)
    second = await process_memory_index_queue(limit=5)
    assert second["memory_queue_succeeded"] == 1
    memory.store_successful_proposal.assert_awaited()

    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(
                select(MemoryIndexPending.id).where(MemoryIndexPending.dedupe_key == dedupe_key)
            )
            is None
        )
        reward_count = await session.scalar(
            select(func.count(Reward.id)).where(Reward.job_id == job_id)
        )
        assert int(reward_count or 0) == 1

    async with AsyncSessionLocal() as session:
        dup = await record_outcome(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="replied",
        )
        await session.commit()
    assert dup.duplicate is True

    async with AsyncSessionLocal() as session:
        pending_after_dup = await session.scalar(
            select(func.count(MemoryIndexPending.id)).where(
                MemoryIndexPending.dedupe_key == dedupe_key
            )
        )
        assert int(pending_after_dup or 0) == 0
        reward_count = await session.scalar(
            select(func.count(Reward.id)).where(Reward.job_id == job_id)
        )
        assert int(reward_count or 0) == 1
