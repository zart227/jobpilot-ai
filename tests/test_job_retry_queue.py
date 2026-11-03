"""Due retries, terminal status protection, and bounded pipeline attempts."""
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.services.job_processing import record_processing_error, try_claim_job
from app.tasks import scrape_tasks

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def test_queue_deduplicates_pending_broker_enqueue(monkeypatch):
    from types import SimpleNamespace

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(
            id=job_id, platform="fake", external_id=str(job_id), title="test", description="test",
            status="new",
        ))
        await session.commit()
    calls: list[str] = []

    def fake_delay(jid: str):
        calls.append(jid)
        return SimpleNamespace(id=f"task-{jid}")

    monkeypatch.setattr(scrape_tasks.process_single_job, "delay", fake_delay)
    monkeypatch.setattr(scrape_tasks, "is_kwork_offers_paused", lambda: False)
    await scrape_tasks._queue_unprocessed_jobs()
    await scrape_tasks._queue_unprocessed_jobs()
    assert calls.count(str(job_id)) == 1
    from app.services.job_task_enqueue import release_process_job_enqueue

    release_process_job_enqueue(str(job_id))


async def test_queue_due_retry_and_exclude_terminal_or_future(monkeypatch):
    now = datetime.now(UTC)
    rows = [
        ("new", None, None, "fake"),
        ("retryable_error", True, now - timedelta(minutes=1), "fake"),
        ("retryable_error", True, now + timedelta(minutes=5), "fake"),
        ("filtered", None, None, "fake"),
        ("permanent_error", None, None, "fake"),
        ("pending_approval", None, None, "fake"),
        ("sent", None, None, "fake"),
        ("scored", True, None, "fake"),
        ("new", None, None, "kwork"),
    ]
    ids = [uuid.uuid4() for _ in rows]
    async with AsyncSessionLocal() as session:
        for job_id, (status, relevant, due, platform) in zip(ids, rows, strict=True):
            session.add(Job(
                id=job_id, platform=platform,
                external_id="kwork-" + str(job_id.int) if platform == "kwork" else str(job_id),
                title="test", description="test", status=status, is_relevant=relevant,
                processing_next_attempt_at=due,
            ))
        await session.commit()
    enqueue = Mock()
    monkeypatch.setattr(scrape_tasks.process_single_job, "delay", enqueue)
    monkeypatch.setattr(scrape_tasks, "is_kwork_offers_paused", lambda: False)
    await scrape_tasks._queue_unprocessed_jobs()
    queued = {call.args[0] for call in enqueue.call_args_list}
    assert {str(ids[i]) for i in (0, 1, 7, 8)} <= queued
    assert not ({str(ids[i]) for i in (2, 3, 4, 5, 6)} & queued)


async def test_queue_skips_active_claim_but_includes_expired(monkeypatch):
    now = datetime.now(UTC)
    ids = [uuid.uuid4(), uuid.uuid4()]
    async with AsyncSessionLocal() as session:
        for job_id, age in zip(ids, (0, get_settings().job_processing_lease_seconds + 60), strict=True):
            session.add(Job(
                id=job_id, platform="fake", external_id=str(job_id), title="test", description="test",
                status="new", processing_claim_token=uuid.uuid4(),
                processing_claimed_at=now - timedelta(seconds=age),
            ))
        await session.commit()
    enqueue = Mock()
    monkeypatch.setattr(scrape_tasks.process_single_job, "delay", enqueue)
    monkeypatch.setattr(scrape_tasks, "is_kwork_offers_paused", lambda: False)
    await scrape_tasks._queue_unprocessed_jobs()
    queued = {call.args[0] for call in enqueue.call_args_list}
    assert str(ids[0]) not in queued and str(ids[1]) in queued


@pytest.mark.parametrize("status", ["filtered", "permanent_error", "approved", "sent"])
async def test_terminal_or_protected_job_cannot_be_claimed(status):
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="test",
                        description="test", status=status))
        await session.commit()
    assert await try_claim_job(job_id, owner="test") is None


async def test_retry_cap_and_backoff(monkeypatch):
    monkeypatch.setenv("JOB_PROCESSING_MAX_ATTEMPTS", "5")
    get_settings.cache_clear()
    ids = [uuid.uuid4(), uuid.uuid4()]
    async with AsyncSessionLocal() as session:
        session.add_all([
            Job(id=ids[0], platform="fake", external_id=str(ids[0]), title="test", description="test",
                status="retryable_error", processing_attempt_count=3),
            Job(id=ids[1], platform="fake", external_id=str(ids[1]), title="test", description="test",
                status="retryable_error", processing_attempt_count=4),
        ])
        await session.commit()
    for job_id in ids:
        claim = await try_claim_job(job_id, owner="test")
        assert claim
        await record_processing_error(job_id, claim.claim_token, stage="scoring", error="offline", retryable=True)
    async with AsyncSessionLocal() as session:
        jobs = {job.id: job for job in (await session.scalars(select(Job).where(Job.id.in_(ids)))).all()}
        assert jobs[ids[0]].status == "retryable_error"
        assert jobs[ids[0]].processing_next_attempt_at > datetime.now(UTC)
        assert jobs[ids[1]].status == "permanent_error"
        assert jobs[ids[1]].processing_error_type == "retry_exhausted"
        assert jobs[ids[1]].processing_next_attempt_at is None
    assert await try_claim_job(ids[1], owner="test") is None

async def test_graph_initialization_failure_is_recorded_and_backed_off(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="test",
                        description="test", status="new"))
        await session.commit()
    monkeypatch.setattr(scrape_tasks, "JobPipelineService", lambda: SimpleNamespace(
        is_job_sent=AsyncMock(return_value=False)
    ))

    def fail():
        raise RuntimeError("graph unavailable")

    monkeypatch.setattr(scrape_tasks, "compile_jobpilot_graph", fail)
    with pytest.raises(RuntimeError, match="graph unavailable"):
        await scrape_tasks._process_job(job_id)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.status == "retryable_error"
        assert job.processing_stage == "pipeline"
        assert job.processing_claim_token is None
        assert job.processing_next_attempt_at > datetime.now(UTC)


async def test_graph_reported_error_respects_retry_cap(monkeypatch):
    from app.services.job_pipeline import JobPipelineService

    monkeypatch.setenv("JOB_PROCESSING_MAX_ATTEMPTS", "5")
    get_settings.cache_clear()
    job_id, token = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="test",
                        description="test", status="new", processing_claim_token=token,
                        processing_claimed_at=datetime.now(UTC), processing_attempt_count=5))
        await session.commit()
    await JobPipelineService().persist_pipeline_results(job_id, {
        "pipeline_processing_error": True, "processing_retryable": True,
        "processing_stage": "proposal", "error": "offline",
    }, claim_token=token)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.status == "permanent_error" and job.processing_error_type == "retry_exhausted"
        assert job.processing_claim_token is None and job.processing_next_attempt_at is None

async def test_operator_requeue_rejects_claims_and_delivery_artifacts():
    from app.db.models import Proposal, SendAttempt
    from app.services.job_processing import requeue_exhausted_job

    ids = [uuid.uuid4() for _ in range(5)]
    async with AsyncSessionLocal() as session:
        for index, job_id in enumerate(ids):
            session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="test",
                            description="test", status="permanent_error", processing_attempt_count=5,
                            processing_error_type="permanent" if index == 4 else "retry_exhausted",
                            processing_claim_token=uuid.uuid4() if index == 3 else None))
        await session.flush()
        session.add(Proposal(id=uuid.uuid4(), job_id=ids[1], content="existing", status="draft", version=1))
        session.add(SendAttempt(id=uuid.uuid4(), job_id=ids[2], kind="proposal",
                                idempotency_key=f"test-{uuid.uuid4()}", snapshot_content="test",
                                status="known_failed", platform_response={}))
        await session.commit()
    async with AsyncSessionLocal() as session:
        assert await requeue_exhausted_job(session, ids[0])
        for job_id in ids[1:]:
            assert not await requeue_exhausted_job(session, job_id)
        await session.commit()
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, ids[0])
        assert job.status == "retryable_error" and job.processing_attempt_count == 0
        assert job.raw_data["operator_retry_requeue"]["previous_attempts"] == 5
        assert job.processing_next_attempt_at is None
        assert not await requeue_exhausted_job(session, ids[0])


async def test_read_only_inventory_includes_preexisting_high_attempts(monkeypatch, capsys):
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.setenv("JOB_PROCESSING_MAX_ATTEMPTS", "5")
    get_settings.cache_clear()
    job_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="test",
                        description="test", status="new", processing_attempt_count=5))
        await session.commit()
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "ops_job_retry_recovery.py"))
    assert await module["main"](SimpleNamespace(id=job_id, apply=False)) == 0
    assert str(job_id) in capsys.readouterr().out
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.status == "new" and job.processing_attempt_count == 5

async def test_expired_claim_cannot_persist_or_record_error():
    from app.services.job_pipeline import JobPipelineService

    job_id, token = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="test",
                        description="test", status="new", processing_claim_token=token,
                        processing_claimed_at=datetime.now(UTC) - timedelta(hours=2),
                        processing_attempt_count=1))
        await session.commit()
    await JobPipelineService().persist_pipeline_results(
        job_id, {"is_relevant": False, "relevance_reason": "late"}, claim_token=token
    )
    await record_processing_error(job_id, token, stage="late", error="late", retryable=True)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        assert job.status == "new" and job.processing_claim_token == token
        assert job.is_relevant is None and job.processing_error_type is None


async def test_job_graph_heartbeat_prevents_expiry(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from app.services import job_processing

    job_id, token = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform="fake", external_id=str(job_id), title="test",
                        description="test", status="new", processing_claim_token=token,
                        processing_claimed_at=datetime.now(UTC), processing_attempt_count=1))
        await session.commit()
    monkeypatch.setattr(job_processing, "get_settings", lambda: SimpleNamespace(
        job_processing_lease_seconds=3, job_processing_max_attempts=5,
    ))
    started, release = asyncio.Event(), asyncio.Event()

    async def run(state):
        started.set()
        await release.wait()
        return {"ok": True}

    claim = job_processing.JobClaim(job_id=job_id, claim_token=token, attempt_id=uuid.uuid4())
    task = asyncio.create_task(job_processing.run_job_graph_with_claim(SimpleNamespace(ainvoke=run), {}, claim))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(4)
        assert await try_claim_job(job_id, owner="other") is None
        release.set()
        assert await asyncio.wait_for(task, timeout=5) == {"ok": True}
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_lost_job_heartbeat_cancels_graph(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services import job_processing

    monkeypatch.setattr(job_processing, "get_settings", lambda: SimpleNamespace(job_processing_lease_seconds=0.3))
    monkeypatch.setattr(job_processing, "renew_job_claim", AsyncMock(side_effect=[True, False]))
    cancelled = asyncio.Event()

    async def run(state):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    claim = job_processing.JobClaim(
        job_id=uuid.uuid4(), claim_token=uuid.uuid4(), attempt_id=uuid.uuid4(),
    )
    with pytest.raises(job_processing.JobClaimLost):
        await asyncio.wait_for(job_processing.run_job_graph_with_claim(SimpleNamespace(ainvoke=run), {}, claim), 5)
    assert cancelled.is_set()
