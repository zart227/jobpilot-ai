"""Synchronous Qdrant calls must not block the asyncio event loop."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.memory.qdrant_store import QdrantMemoryStore


@pytest.mark.parametrize("operation", ["upsert", "search"])
async def test_slow_qdrant_io_does_not_block_event_loop(operation):
    started = threading.Event()
    release = threading.Event()
    store = object.__new__(QdrantMemoryStore)
    store._settings = SimpleNamespace(embedding_model="test", embedding_provider="test")
    store._prefix = "test"
    store._vector_dim = 1
    store._embed = AsyncMock(return_value=[1.0])

    def block(*args, **kwargs):
        started.set()
        if not release.wait(timeout=4):
            raise RuntimeError("test timed out waiting for release")
        return []

    store._client = SimpleNamespace(upsert=block)
    store._vector_search = block
    if operation == "upsert":
        task = asyncio.create_task(store.store_job_embedding("1", "test", {}))
    else:
        task = asyncio.create_task(store.search_similar_proposals("test"))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
        await asyncio.wait_for(asyncio.sleep(0.02), timeout=0.5)
        assert not task.done()
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=3)
