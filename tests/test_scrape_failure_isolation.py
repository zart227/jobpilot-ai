"""An individual source or job failure must not stop unrelated scraped jobs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.tasks import scrape_tasks


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(scrape_tasks, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(scrape_tasks, "recover_stale_job_claims", AsyncMock())
    monkeypatch.setattr(scrape_tasks, "record_processing_error", AsyncMock())
    monkeypatch.setattr(scrape_tasks, "is_kwork_offers_paused", lambda: False)
    monkeypatch.setattr(scrape_tasks, "_refresh_kwork_pause_state", AsyncMock(return_value={}))
    monkeypatch.setattr(scrape_tasks, "_queue_unprocessed_jobs", AsyncMock(return_value=0))
    monkeypatch.setattr(scrape_tasks, "compile_jobpilot_graph", lambda: object())

    async def run_graph(graph, state, claim):
        return await graph.ainvoke(state)

    monkeypatch.setattr(scrape_tasks, "run_job_graph_with_claim", run_graph)


async def test_failed_scraper_does_not_stop_other_platform(environment, monkeypatch):
    bad = SimpleNamespace(scrape=AsyncMock(side_effect=RuntimeError("source down")))
    job = SimpleNamespace(platform="fake", external_id="good")
    good = SimpleNamespace(scrape=AsyncMock(return_value=[job]))
    monkeypatch.setattr(scrape_tasks, "get_scrapers", lambda: [bad, good])
    pipeline = SimpleNamespace(save_normalized_job=AsyncMock(return_value="good-id"),
                               is_job_sent=AsyncMock(return_value=True))
    monkeypatch.setattr(scrape_tasks, "JobPipelineService", lambda: pipeline)
    result = await scrape_tasks._scrape_and_process()
    assert result["scraped"] == 1
    assert result["failed_sources"] == 1
    pipeline.save_normalized_job.assert_awaited_once_with(job)


@pytest.mark.parametrize("stage", ["save", "is_sent", "claim"])
async def test_failed_job_does_not_stop_next_job(environment, monkeypatch, stage):
    first = SimpleNamespace(platform="fake", external_id="bad")
    second = SimpleNamespace(platform="fake", external_id="good")
    monkeypatch.setattr(scrape_tasks, "get_scrapers", lambda: [SimpleNamespace(scrape=AsyncMock(return_value=[first, second]))])
    pipeline = SimpleNamespace(save_normalized_job=AsyncMock(side_effect=["bad-id", "good-id"]),
                               is_job_sent=AsyncMock(return_value=True))
    claim = AsyncMock(return_value=None)
    if stage == "save":
        pipeline.save_normalized_job.side_effect = [RuntimeError("cannot persist"), "good-id"]
    elif stage == "is_sent":
        pipeline.is_job_sent.side_effect = [RuntimeError("cannot inspect"), True]
    else:
        pipeline.is_job_sent.side_effect = [False, True]
        claim.side_effect = RuntimeError("cannot claim")
    monkeypatch.setattr(scrape_tasks, "JobPipelineService", lambda: pipeline)
    monkeypatch.setattr(scrape_tasks, "try_claim_job", claim)
    result = await scrape_tasks._scrape_and_process()
    assert pipeline.save_normalized_job.await_count == 2
    assert "good-id" in scrape_tasks._queue_unprocessed_jobs.await_args.kwargs["exclude"]
    assert result["scraped"] >= 1
    assert result["failed_jobs"] == 1


@pytest.mark.parametrize("stage", ["graph", "release"])
async def test_pipeline_or_release_failure_does_not_abort_next_job(environment, monkeypatch, stage):
    from app.db import session as db_session
    from app.services import job_freshness

    first = SimpleNamespace(platform="fake", external_id="bad")
    second = SimpleNamespace(platform="fake", external_id="good")
    monkeypatch.setattr(scrape_tasks, "get_scrapers", lambda: [SimpleNamespace(scrape=AsyncMock(return_value=[first, second]))])
    pipeline = SimpleNamespace(
        save_normalized_job=AsyncMock(side_effect=["bad-id", "good-id"]),
        is_job_sent=AsyncMock(side_effect=[False, True]),
        job_to_state=AsyncMock(return_value={}),
        persist_pipeline_results=AsyncMock(return_value=None),
    )
    graph = SimpleNamespace(ainvoke=AsyncMock(return_value={"is_relevant": False}))
    release = AsyncMock()
    if stage == "graph":
        graph.ainvoke.side_effect = RuntimeError("graph failed")
    else:
        release.side_effect = RuntimeError("release failed")
    session = AsyncMock()
    session.__aenter__.return_value = session
    monkeypatch.setattr(db_session, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(job_freshness, "skip_if_stale", AsyncMock(return_value=None))
    monkeypatch.setattr(scrape_tasks, "JobPipelineService", lambda: pipeline)
    monkeypatch.setattr(scrape_tasks, "compile_jobpilot_graph", lambda: graph)
    monkeypatch.setattr(scrape_tasks, "try_claim_job", AsyncMock(return_value=SimpleNamespace(job_id="bad-id", claim_token="token")))
    monkeypatch.setattr(scrape_tasks, "release_job_claim", release)
    result = await scrape_tasks._scrape_and_process()
    assert result["failed_jobs"] == 1
    assert pipeline.save_normalized_job.await_count == 2
    if stage == "graph":
        scrape_tasks.record_processing_error.assert_awaited_once()
    release.assert_awaited_once()
