"""Job embedding failures must remain recoverable without holding DB sessions."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.llm.embeddings import EmbeddingUnavailableError
from app.memory.qdrant_store import QdrantMemoryStore
from app.schemas.job import NormalizedJob
from app.services import job_pipeline
from app.tasks import embedding_tasks


async def test_unavailable_embedding_is_reported_as_not_stored():
    store = QdrantMemoryStore.__new__(QdrantMemoryStore)
    store._embed = AsyncMock(side_effect=EmbeddingUnavailableError("offline"))
    store._client = MagicMock()
    assert await store.store_job_embedding("test", "text", {}) is False
    store._client.upsert.assert_not_called()


@pytest.fixture
def tracked_sessions(monkeypatch):
    active = {"count": 0}

    class Tracked:
        async def __aenter__(self):
            self.session = AsyncSessionLocal()
            await self.session.__aenter__()
            active["count"] += 1
            return self.session

        async def __aexit__(self, *args):
            try:
                return await self.session.__aexit__(*args)
            finally:
                active["count"] -= 1

    monkeypatch.setattr(job_pipeline, "AsyncSessionLocal", Tracked)
    return active


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
async def test_deferred_embedding_persists_pending_without_open_session(tracked_sessions, monkeypatch):
    observed = []

    async def store(*args, **kwargs):
        observed.append(tracked_sessions["count"])
        return False

    monkeypatch.setattr(job_pipeline, "get_memory_store", lambda: SimpleNamespace(store_job_embedding=store))
    normalized = NormalizedJob(platform="fake", external_id=str(uuid.uuid4()), title="t", description="d")
    job_id = await job_pipeline.JobPipelineService().save_normalized_job(normalized)
    assert observed == [0]
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).raw_data["embedding_pending"] is True


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
async def test_job_changed_during_embedding_remains_pending(tracked_sessions, monkeypatch):
    async def store(job_id, *args, **kwargs):
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, uuid.UUID(job_id))
            job.description = "changed while embedding"
            await session.commit()
        return True

    monkeypatch.setattr(job_pipeline, "get_memory_store", lambda: SimpleNamespace(store_job_embedding=store))
    normalized = NormalizedJob(platform="fake", external_id=str(uuid.uuid4()), title="t", description="original")
    job_id = await job_pipeline.JobPipelineService().save_normalized_job(normalized)
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).raw_data["embedding_pending"] is True


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
async def test_retry_does_not_clear_pending_for_unavailable_embedding(monkeypatch):
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="t", description="d",
                        raw_data={"embedding_pending": True}))
        await session.commit()
    memory = SimpleNamespace(store_job_embedding=AsyncMock(return_value=False))
    monkeypatch.setattr(embedding_tasks, "get_memory_store", lambda: memory)
    await embedding_tasks._reindex_pending_embeddings(limit=500)
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).raw_data["embedding_pending"] is True


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
async def test_successful_embedding_clears_pending(tracked_sessions, monkeypatch):
    memory = SimpleNamespace(store_job_embedding=AsyncMock(return_value=True))
    monkeypatch.setattr(job_pipeline, "get_memory_store", lambda: memory)
    normalized = NormalizedJob(platform="fake", external_id=str(uuid.uuid4()), title="t", description="d")
    job_id = await job_pipeline.JobPipelineService().save_normalized_job(normalized)
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).raw_data["embedding_pending"] is False


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
async def test_retry_requeues_job_changed_during_embedding(monkeypatch):
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="t", description="old",
                        raw_data={"embedding_pending": True}, created_at=datetime(2000, 1, 1, tzinfo=UTC)))
        await session.commit()

    async def store(candidate_id, *args):
        if candidate_id == str(job_id):
            async with AsyncSessionLocal() as session:
                row = await session.get(Job, job_id)
                row.description = "changed"
                await session.commit()
        return True

    monkeypatch.setattr(embedding_tasks, "get_memory_store", lambda: SimpleNamespace(store_job_embedding=store))
    await embedding_tasks._reindex_pending_embeddings(limit=1000)
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).raw_data["embedding_pending"] is True


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
async def test_old_pending_job_is_not_hidden_by_500_newer_jobs(monkeypatch):
    job_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="old", description="d",
                        raw_data={"embedding_pending": True}, created_at=now - timedelta(days=1000)))
        session.add_all([
            Job(id=uuid.uuid4(), platform="fake", external_id=f"newer-{job_id}-{index}",
                title="recent", description="d", raw_data={}, created_at=now - timedelta(days=1))
            for index in range(501)
        ])
        await session.commit()
    memory = SimpleNamespace(store_job_embedding=AsyncMock(return_value=True))
    monkeypatch.setattr(embedding_tasks, "get_memory_store", lambda: memory)
    await embedding_tasks._reindex_pending_embeddings(limit=1000)
    assert str(job_id) in {call.args[0] for call in memory.store_job_embedding.await_args_list}
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).raw_data["embedding_pending"] is False
