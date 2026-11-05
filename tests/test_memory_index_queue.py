"""Durable SQL queue retries proposal/phrase/edit vector indexing."""

import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.db.models import MemoryIndexPending
from app.db.session import AsyncSessionLocal
from app.services.memory_index_queue import (
    KIND_EDIT_PREFERENCE,
    KIND_PRICING_PHRASE,
    KIND_SUCCESSFUL_PROPOSAL,
    enqueue_memory_index,
    process_memory_index_queue,
    recover_stale_memory_index_claims,
)
from app.tasks import embedding_tasks

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


@pytest.fixture(autouse=True)
async def clear_memory_index_queue():
    from sqlalchemy import delete

    async with AsyncSessionLocal() as session:
        await session.execute(delete(MemoryIndexPending))
        await session.commit()


async def test_enqueue_is_idempotent_by_dedupe_key():
    key = f"proposal:{uuid.uuid4()}"
    payload = {"memory_key": key, "content": "text", "metadata": {}}
    assert await enqueue_memory_index(KIND_SUCCESSFUL_PROPOSAL, key, payload)
    assert not await enqueue_memory_index(KIND_SUCCESSFUL_PROPOSAL, key, payload)
    async with AsyncSessionLocal() as session:
        count = await session.scalar(select(func.count(MemoryIndexPending.id)))
        assert count == 1


async def test_process_queue_removes_successful_row(monkeypatch):
    key = f"proposal:{uuid.uuid4()}"
    await enqueue_memory_index(
        KIND_SUCCESSFUL_PROPOSAL,
        key,
        {"memory_key": key, "content": "saved proposal", "metadata": {"outcome": "replied"}},
    )
    memory = AsyncMock()
    memory.store_successful_proposal = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.memory_index_queue.get_memory_store", lambda: memory)
    stats = await process_memory_index_queue(limit=10)
    assert stats["memory_queue_succeeded"] == 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count(MemoryIndexPending.id))) == 0
    memory.store_successful_proposal.assert_awaited_once()


async def test_failed_index_keeps_row_and_records_error(monkeypatch):
    key = f"edit:{uuid.uuid4()}"
    await enqueue_memory_index(
        KIND_EDIT_PREFERENCE,
        key,
        {
            "instruction": "shorten",
            "original_content": "long text",
            "edited_content": "short",
            "metadata": {},
        },
    )
    memory = AsyncMock()
    memory.store_edit_preference = AsyncMock(return_value=False)
    monkeypatch.setattr("app.services.memory_index_queue.get_memory_store", lambda: memory)
    stats = await process_memory_index_queue(limit=10)
    assert stats["memory_queue_failed"] == 1
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(MemoryIndexPending).where(MemoryIndexPending.dedupe_key == key))
        assert row and row.last_error == "index deferred"


async def test_recovery_drill_outage_then_beat_retry(monkeypatch):
    """Simulates Qdrant outage (failed upsert) followed by successful reindex pass."""
    key = f"proposal:{uuid.uuid4()}"
    await enqueue_memory_index(
        KIND_SUCCESSFUL_PROPOSAL,
        key,
        {"memory_key": key, "content": "drill text", "metadata": {"outcome": "replied"}},
    )
    memory = AsyncMock()
    memory.store_successful_proposal = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr("app.services.memory_index_queue.get_memory_store", lambda: memory)
    first = await process_memory_index_queue(limit=5)
    assert first["memory_queue_failed"] == 1
    second = await process_memory_index_queue(limit=5)
    assert second["memory_queue_succeeded"] == 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count(MemoryIndexPending.id))) == 0


@pytest.mark.parametrize(
    "kind,payload,store_attr",
    [
        (
            KIND_SUCCESSFUL_PROPOSAL,
            {"memory_key": "k", "content": "body", "metadata": {}},
            "store_successful_proposal",
        ),
        (KIND_PRICING_PHRASE, {"phrase": "fixed price", "score": 0.9}, "store_pricing_phrase"),
        (
            KIND_EDIT_PREFERENCE,
            {
                "instruction": "shorter",
                "original_content": "long",
                "edited_content": "short",
                "metadata": {},
            },
            "store_edit_preference",
        ),
    ],
)
async def test_all_memory_index_kinds_process_through_queue(monkeypatch, kind, payload, store_attr):
    """C060: each deferred index kind is applied via the SQL queue worker."""
    dedupe = f"{kind}:{uuid.uuid4()}"
    full_payload = {**payload}
    if kind == KIND_SUCCESSFUL_PROPOSAL:
        full_payload["memory_key"] = dedupe
    await enqueue_memory_index(kind, dedupe, full_payload)
    memory = AsyncMock()
    setattr(memory, store_attr, AsyncMock(return_value=True))
    monkeypatch.setattr("app.services.memory_index_queue.get_memory_store", lambda: memory)
    stats = await process_memory_index_queue(limit=5)
    assert stats["memory_queue_succeeded"] == 1
    getattr(memory, store_attr).assert_awaited_once()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count(MemoryIndexPending.id))) == 0


async def test_reindex_task_processes_memory_queue(monkeypatch):
    key = f"proposal:{uuid.uuid4()}"
    await enqueue_memory_index(
        KIND_SUCCESSFUL_PROPOSAL,
        key,
        {"memory_key": key, "content": "x", "metadata": {}},
    )
    monkeypatch.setattr(embedding_tasks, "_reindex_job_embeddings", AsyncMock(return_value={"indexed": 0}))
    memory = AsyncMock()
    memory.store_successful_proposal = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.memory_index_queue.get_memory_store", lambda: memory)
    result = await embedding_tasks._reindex_pending_embeddings(limit=5)
    assert result["memory_queue_succeeded"] == 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count(MemoryIndexPending.id))) == 0


async def test_enqueue_refreshes_payload_when_dedupe_key_reused():
    key = f"proposal:shared:{uuid.uuid4()}"
    base = {"memory_key": key, "metadata": {}}
    assert await enqueue_memory_index(
        KIND_SUCCESSFUL_PROPOSAL, key, {**base, "content": "version-one"}
    )
    assert not await enqueue_memory_index(
        KIND_SUCCESSFUL_PROPOSAL, key, {**base, "content": "version-one"}
    )
    assert await enqueue_memory_index(
        KIND_SUCCESSFUL_PROPOSAL, key, {**base, "content": "version-two"}
    )
    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(MemoryIndexPending).where(MemoryIndexPending.dedupe_key == key)
        )
        assert row and row.payload["content"] == "version-two"


async def test_concurrent_workers_index_same_row_once(monkeypatch):
    import asyncio

    key = f"proposal:{uuid.uuid4()}"
    await enqueue_memory_index(
        KIND_SUCCESSFUL_PROPOSAL,
        key,
        {"memory_key": key, "content": "race", "metadata": {}},
    )
    lock = asyncio.Lock()
    calls = 0

    async def counting_store(*_args, **_kwargs):
        nonlocal calls
        async with lock:
            calls += 1
        await asyncio.sleep(0.05)
        return True

    memory = AsyncMock()
    memory.store_successful_proposal = counting_store
    monkeypatch.setattr("app.services.memory_index_queue.get_memory_store", lambda: memory)

    first, second = await asyncio.gather(
        process_memory_index_queue(limit=5),
        process_memory_index_queue(limit=5),
    )
    assert calls == 1
    assert first["memory_queue_succeeded"] + second["memory_queue_succeeded"] == 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count(MemoryIndexPending.id))) == 0


async def test_stale_claim_released_before_reclaim(monkeypatch):
    from datetime import UTC, datetime, timedelta

    key = f"proposal:{uuid.uuid4()}"
    row_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            MemoryIndexPending(
                id=row_id,
                kind=KIND_SUCCESSFUL_PROPOSAL,
                dedupe_key=key,
                payload={"memory_key": key, "content": "stale", "metadata": {}},
                claim_token=uuid.uuid4(),
                claim_expires_at=datetime.now(UTC) - timedelta(minutes=1),
                claim_owner="memory_index_worker",
            )
        )
        await session.commit()

    assert await recover_stale_memory_index_claims() == 1
    memory = AsyncMock()
    memory.store_successful_proposal = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.memory_index_queue.get_memory_store", lambda: memory)
    stats = await process_memory_index_queue(limit=5)
    assert stats["memory_queue_succeeded"] == 1


async def test_recover_stale_claims_does_not_clear_fresh_claim():
    from datetime import UTC, datetime, timedelta

    key = f"proposal:{uuid.uuid4()}"
    fresh_token = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            MemoryIndexPending(
                kind=KIND_SUCCESSFUL_PROPOSAL,
                dedupe_key=key,
                payload={"memory_key": key, "content": "fresh", "metadata": {}},
                claim_token=fresh_token,
                claim_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                claim_owner="memory_index_worker",
            )
        )
        await session.commit()

    assert await recover_stale_memory_index_claims() == 0

    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(MemoryIndexPending).where(MemoryIndexPending.dedupe_key == key)
        )
        assert row is not None
        assert row.claim_token == fresh_token
