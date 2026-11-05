"""Isolated restart/lease drills for job processing claims (C053/C058 evidence)."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings, get_settings
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.services.job_processing import (
    recover_stale_job_claims,
    try_claim_job,
)
from app.services.job_task_enqueue import (
    release_process_job_enqueue,
    try_reserve_process_job_enqueue,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


@pytest.fixture
def short_lease_settings(monkeypatch):
    monkeypatch.setenv("JOB_PROCESSING_LEASE_SECONDS", "60")
    get_settings.cache_clear()
    return Settings(_env_file=None, job_processing_lease_seconds=60)


async def test_worker_outage_drill_dedup_blocks_until_stale_recovery(short_lease_settings):
    """Simulated worker crash: broker dedup held until stale claim recovery (C052/C053)."""
    from app.services.job_task_enqueue import queue_process_single_job

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(
            id=job_id, platform="fake", external_id=str(job_id), title="t", description="d", status="new",
        ))
        await session.commit()

    assert queue_process_single_job(str(job_id)) is not None
    assert queue_process_single_job(str(job_id)) is None

    claim = await try_claim_job(job_id, owner="crashed-worker")
    assert claim
    assert queue_process_single_job(str(job_id)) is None

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.processing_claimed_at = datetime.now(UTC) - timedelta(seconds=120)
        await session.commit()

    assert await recover_stale_job_claims(short_lease_settings) >= 1
    assert queue_process_single_job(str(job_id)) is not None

    release_process_job_enqueue(str(job_id))


async def test_process_single_job_restart_after_simulated_worker_death(short_lease_settings, monkeypatch):
    """Second worker completes job after stale recovery clears orphaned claim+dedup (C053 E2E)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.job_task_enqueue import queue_process_single_job
    from app.tasks import scrape_tasks

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(
            id=job_id, platform="fake", external_id=str(job_id), title="t", description="d", status="new",
        ))
        await session.commit()

    assert queue_process_single_job(str(job_id)) is not None
    claim1 = await try_claim_job(job_id, owner="worker-a")
    assert claim1

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.processing_claimed_at = datetime.now(UTC) - timedelta(seconds=120)
        await session.commit()

    assert await recover_stale_job_claims(short_lease_settings) >= 1

    pipeline = SimpleNamespace(
        is_job_sent=AsyncMock(return_value=False),
        job_to_state=AsyncMock(return_value={"job_data": {"platform": "fake", "title": "t", "description": "d"}}),
        persist_pipeline_results=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(scrape_tasks, "JobPipelineService", lambda: pipeline)
    monkeypatch.setattr(scrape_tasks, "compile_jobpilot_graph", lambda: object())
    monkeypatch.setattr(
        scrape_tasks,
        "run_job_graph_with_claim",
        AsyncMock(return_value={"is_relevant": False, "relevance_reason": "drill"}),
    )
    monkeypatch.setattr(scrape_tasks, "is_kwork_offers_paused", lambda: False)

    result = await scrape_tasks._process_job(job_id)
    assert result["job_id"] == str(job_id)

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.processing_claim_token is None
        journal = job.raw_data.get("processing_attempt_journal") or []
        events = [row["event"] for row in journal]
        assert "stale_claim_recovered" in events
        assert events.count("claimed") >= 2
        attempt_ids = {row.get("attempt_id") for row in journal if row.get("event") == "claimed"}
        assert len(attempt_ids) >= 2

    pipeline.persist_pipeline_results.assert_awaited_once()
    release_process_job_enqueue(str(job_id))


async def test_stale_recovery_clears_enqueue_dedup_and_allows_new_claim(short_lease_settings):
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(
            id=job_id, platform="fake", external_id=str(job_id), title="t", description="d", status="new",
        ))
        await session.commit()

    assert try_reserve_process_job_enqueue(str(job_id))
    claim = await try_claim_job(job_id, owner="worker-a")
    assert claim

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.processing_claimed_at = datetime.now(UTC) - timedelta(seconds=120)
        await session.commit()

    assert await recover_stale_job_claims(short_lease_settings) >= 1
    assert try_reserve_process_job_enqueue(str(job_id))

    claim2 = await try_claim_job(job_id, owner="worker-b")
    assert claim2 and claim2.claim_token != claim.claim_token

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        journal = job.raw_data.get("processing_attempt_journal") or []
        events = [row["event"] for row in journal]
        assert "claimed" in events and "stale_claim_recovered" in events
        assert job.processing_attempt_count == 2

    release_process_job_enqueue(str(job_id))


async def test_beat_queue_cycle_after_stale_recovery_re_enqueues_once(short_lease_settings, monkeypatch):
    """C052: beat recover_stale + _queue_unprocessed_jobs after worker death re-enqueues once."""
    from types import SimpleNamespace

    from app.tasks import scrape_tasks

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(
            id=job_id, platform="fake", external_id=str(job_id), title="t", description="d", status="new",
        ))
        await session.commit()

    job_key = str(job_id)
    job_enqueue_calls: list[str] = []

    def fake_delay(jid: str):
        if jid == job_key:
            job_enqueue_calls.append(jid)
        return SimpleNamespace(id=f"task-{jid}")

    monkeypatch.setattr(scrape_tasks.process_single_job, "delay", fake_delay)
    monkeypatch.setattr(scrape_tasks, "is_kwork_offers_paused", lambda: False)

    await scrape_tasks._queue_unprocessed_jobs()
    assert job_enqueue_calls == [job_key]
    claim = await try_claim_job(job_id, owner="dead-worker")
    assert claim
    await scrape_tasks._queue_unprocessed_jobs()
    assert job_enqueue_calls == [job_key]

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.processing_claimed_at = datetime.now(UTC) - timedelta(seconds=400)
        await session.commit()

    assert await recover_stale_job_claims(short_lease_settings) >= 1
    await scrape_tasks._queue_unprocessed_jobs()
    assert job_enqueue_calls == [job_key, job_key]

    release_process_job_enqueue(job_key)


async def test_terminal_filtered_job_not_reclaimed_after_stale_recovery(short_lease_settings):
    job_id = uuid.uuid4()
    token = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(
            id=job_id, platform="fake", external_id=str(job_id), title="t", description="d",
            status="filtered", processing_claim_token=token,
            processing_claimed_at=datetime.now(UTC) - timedelta(hours=2),
        ))
        await session.commit()

    await recover_stale_job_claims(short_lease_settings)
    assert await try_claim_job(job_id, owner="worker") is None

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.status == "filtered"
        assert job.processing_claim_token is None


async def test_llm_stage_log_includes_processing_attempt(short_lease_settings):
    from app.services.pipeline_llm_ledger import persist_stage_log

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(
            id=job_id, platform="fake", external_id=str(job_id), title="t", description="d", status="new",
        ))
        await session.commit()

    claim = await try_claim_job(job_id, owner="test")
    assert claim

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        persist_stage_log(job, {"llm_stage_log": [{"stage": "filter", "bucket": "simple", "provider": "x", "at": "t"}]})
        await session.commit()

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        entry = job.raw_data["llm_stage_log"][0]
        assert entry["processing_attempt"] == 1
        assert entry["processing_attempt_id"] == job.raw_data["current_processing_attempt_id"]

    release_process_job_enqueue(str(job_id))
