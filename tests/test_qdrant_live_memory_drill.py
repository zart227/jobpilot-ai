"""Optional live Qdrant drill: SQL memory queue → real upsert (no production DB writes beyond test row)."""

import os
import uuid

import httpx
import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
    ),
    pytest.mark.skipif(
        os.environ.get("JOBPILOT_QDRANT_DRILL") != "1",
        reason="set JOBPILOT_QDRANT_DRILL=1 to probe compose Qdrant",
    ),
]


async def test_live_qdrant_memory_queue_recovery_drill(monkeypatch):
    from sqlalchemy import func, select

    from app.config import get_settings
    from app.db.models import MemoryIndexPending
    from app.db.session import AsyncSessionLocal
    from app.llm import embeddings
    from app.services.memory_index_queue import (
        KIND_SUCCESSFUL_PROPOSAL,
        enqueue_memory_index,
        process_memory_index_queue,
    )

    settings = get_settings()
    url = settings.qdrant_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{url}/collections")
            response.raise_for_status()
    except Exception as exc:
        pytest.skip(f"Qdrant not reachable at {url}: {exc}")

    dim = settings.embedding_dimensions

    async def fake_embed(text: str, _settings):
        return [0.01] * dim

    monkeypatch.setattr(embeddings, "embed_text", fake_embed)

    key = f"drill:{uuid.uuid4()}"
    await enqueue_memory_index(
        KIND_SUCCESSFUL_PROPOSAL,
        key,
        {"memory_key": key, "content": "drill proposal body", "metadata": {"outcome": "replied"}},
    )
    stats = await process_memory_index_queue(limit=5)
    assert stats["memory_queue_succeeded"] >= 1
    async with AsyncSessionLocal() as session:
        remaining = await session.scalar(select(func.count(MemoryIndexPending.id)))
        assert remaining == 0
