"""C052 beat/scrape cycle ordering (recover stale before queue)."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings, get_settings
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.services.job_processing import recover_stale_job_claims, try_claim_job
from app.services.job_task_enqueue import release_process_job_enqueue
from app.tasks import scrape_tasks

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


@pytest.fixture
def short_lease_settings(monkeypatch):
    monkeypatch.setenv("JOB_PROCESSING_LEASE_SECONDS", "60")
    get_settings.cache_clear()
    return Settings(_env_file=None, job_processing_lease_seconds=60)


async def test_scrape_cycle_recover_stale_before_queue(monkeypatch):
    order: list[str] = []

    async def track_recover(*args, **kwargs):
        order.append("recover_stale")
        return 0

    async def track_queue(*args, **kwargs):
        order.append("queue_unprocessed")
        return 0

    monkeypatch.setattr(scrape_tasks, "recover_stale_job_claims", track_recover)
    monkeypatch.setattr(scrape_tasks, "_queue_unprocessed_jobs", track_queue)
    monkeypatch.setattr(scrape_tasks, "is_kwork_offers_paused", lambda: True)
    monkeypatch.setattr(scrape_tasks, "get_kwork_pause_reason", lambda: "test")
    monkeypatch.setattr(scrape_tasks, "get_scrapers", list)
    monkeypatch.setattr(
        scrape_tasks,
        "get_settings",
        lambda: Settings(_env_file=None, telegram_admin_chat_id="", telegram_bot_token=""),
    )

    result = await scrape_tasks._scrape_and_process()
    assert order[0] == "recover_stale"
    assert "queue_unprocessed" in order
    assert result["queued_reprocess"] == 0


async def test_reprocess_pending_calls_stale_recovery_before_queue(monkeypatch):
    order: list[str] = []

    async def track_recover(*args, **kwargs):
        order.append("recover_stale")
        return 0

    async def track_queue(*args, **kwargs):
        order.append("queue_unprocessed")
        return 0

    monkeypatch.setattr(scrape_tasks, "recover_stale_job_claims", track_recover)
    monkeypatch.setattr(scrape_tasks, "_queue_unprocessed_jobs", track_queue)
    monkeypatch.setattr(scrape_tasks, "is_kwork_offers_paused", lambda: False)

    result = await scrape_tasks._reprocess_pending()
    assert order == ["recover_stale", "queue_unprocessed"]
    assert result["queued"] == 0
    assert result["stale_claims_recovered"] == 0


async def test_reprocess_pending_runs_queue_after_stale_recovery(monkeypatch, short_lease_settings):
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=job_id,
                platform="fake",
                external_id=str(job_id),
                title="t",
                description="d",
                status="new",
            )
        )
        await session.commit()

    job_key = str(job_id)
    enqueue_calls: list[str] = []

    def fake_delay(jid: str):
        if jid == job_key:
            enqueue_calls.append(jid)
        return type("T", (), {"id": f"task-{jid}"})()

    monkeypatch.setattr(scrape_tasks.process_single_job, "delay", fake_delay)
    monkeypatch.setattr(scrape_tasks, "is_kwork_offers_paused", lambda: False)

    first = await scrape_tasks._reprocess_pending()
    assert first["queued"] >= 1
    assert enqueue_calls == [job_key]

    claim = await try_claim_job(job_id, owner="dead-worker")
    assert claim

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.processing_claimed_at = datetime.now(UTC) - timedelta(seconds=120)
        await session.commit()

    assert await recover_stale_job_claims(short_lease_settings) >= 1
    result = await scrape_tasks._reprocess_pending()
    assert result["queued"] >= 1
    assert enqueue_calls.count(job_key) == 2

    release_process_job_enqueue(str(job_id))
