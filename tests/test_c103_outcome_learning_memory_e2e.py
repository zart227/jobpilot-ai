"""C103: replied → hired → single rewards → durable successful_proposal memory (queue → store)."""

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
        "insights": ["mock insight"],
        "top_platforms": ["kwork"],
    }
)


@pytest.fixture
def mock_learning_and_memory(monkeypatch: pytest.MonkeyPatch):
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
async def test_c103_replied_then_hired_rewards_once_and_memory_index_durable(
    mock_learning_and_memory,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.db.models import Job, MemoryIndexPending, Proposal, Reward, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.memory_index_queue import (
        KIND_SUCCESSFUL_PROPOSAL,
        process_memory_index_queue,
    )
    from app.services.outcome_service import record_outcome, record_outcome_and_learn

    job_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    snapshot = "Sent proposal text for learning and memory indexing."
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c103-{job_id.hex[:10]}",
                title="C103 outcome job",
                description="Desc",
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
                idempotency_key=f"c103-send:{proposal_id}",
                job_id=job_id,
                proposal_id=proposal_id,
                snapshot_content=snapshot,
                snapshot_version=1,
                status=ATTEMPT_CONFIRMED_SENT,
                platform_response={"dry_run": True},
            )
        )
        await session.commit()

    replied_key = f"{proposal_id}:replied:1"
    hired_key = f"{proposal_id}:hired:1"

    async with AsyncSessionLocal() as session:
        replied = await record_outcome_and_learn(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="replied",
        )
    assert replied.created is True
    assert replied.reward == 5
    assert replied.duplicate is False

    async with AsyncSessionLocal() as session:
        pending_replied = await session.scalar(
            select(MemoryIndexPending).where(MemoryIndexPending.dedupe_key == replied_key)
        )
        assert pending_replied is not None
        assert pending_replied.kind == KIND_SUCCESSFUL_PROPOSAL
        assert pending_replied.payload["content"] == snapshot

    queue_memory = AsyncMock()
    queue_memory.store_successful_proposal = AsyncMock(return_value=True)
    queue_memory.store_pricing_phrase = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.memory_index_queue.get_memory_store",
        lambda: queue_memory,
    )
    stats_replied = await process_memory_index_queue(limit=20)
    assert stats_replied["memory_queue_succeeded"] >= 1
    queue_memory.store_successful_proposal.assert_awaited()
    stored_keys = [
        call.args[0]
        for call in queue_memory.store_successful_proposal.await_args_list
        if call.args
    ]
    assert replied_key in stored_keys

    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count(MemoryIndexPending.id)).where(
                    MemoryIndexPending.dedupe_key == replied_key
                )
            )
            == 0
        )

    async with AsyncSessionLocal() as session:
        hired = await record_outcome_and_learn(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="hired",
        )
    assert hired.created is True
    assert hired.reward == 50

    async with AsyncSessionLocal() as session:
        hired_dup = await record_outcome(
            session,
            job_id=job_id,
            proposal_id=proposal_id,
            status="hired",
        )
        await session.commit()
    assert hired_dup.duplicate is True
    assert hired_dup.reward == 50

    async with AsyncSessionLocal() as session:
        reward_count = await session.scalar(
            select(func.count(Reward.id)).where(Reward.job_id == job_id)
        )
        assert int(reward_count or 0) == 2
        pending_hired = await session.scalar(
            select(MemoryIndexPending).where(MemoryIndexPending.dedupe_key == hired_key)
        )
        assert pending_hired is not None

    queue_memory.store_successful_proposal.reset_mock()
    stats_hired = await process_memory_index_queue(limit=20)
    assert stats_hired["memory_queue_succeeded"] >= 1
    hired_stored = [
        call.args[0]
        for call in queue_memory.store_successful_proposal.await_args_list
        if call.args
    ]
    assert hired_key in hired_stored
    assert replied_key not in hired_stored

    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count(MemoryIndexPending.id)).where(
                    MemoryIndexPending.dedupe_key.in_([replied_key, hired_key])
                )
            )
            == 0
        )

    # Different outcome keys must not collapse to one enqueue (regression for R10).
    assert replied_key != hired_key
    mock_learning_and_memory.store_successful_proposal.assert_awaited()
