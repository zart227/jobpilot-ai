import asyncio
import uuid
from typing import Any

import structlog

from app.agents.graph import compile_jobpilot_graph
from app.celery_app import celery_app
from app.config import get_settings
from app.scrapers.registry import get_scrapers
from app.services.job_pipeline import JobPipelineService
from app.services.kwork_browser import refresh_kwork_pause_from_kwork
from app.services.kwork_pause import KWORK_PLATFORM, get_kwork_pause_reason, is_kwork_paused
from app.telegram.bot import notify_new_proposal
from app.llm.ollama_usage import maybe_notify_ollama_usage_warning
from app.utils.proxy import create_telegram_bot

logger = structlog.get_logger(__name__)


def _run_async(coro):
    from app.db.session import engine

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.run_until_complete(engine.dispose())
        loop.close()


@celery_app.task(name="app.tasks.scrape_tasks.run_all_scrapers", bind=True, max_retries=3)
def run_all_scrapers(self) -> dict:
    logger.info("JobPilot AI scrape task started")
    try:
        return _run_async(_scrape_and_process())
    except Exception as exc:
        logger.error("Scrape task failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


async def _queue_unprocessed_jobs(exclude: set[str] | None = None) -> int:
    from sqlalchemy import Integer, and_, cast, desc, func, or_, select

    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal

    exclude = exclude or set()
    kwork_project_id = cast(
        func.nullif(func.regexp_replace(Job.external_id, r"[^0-9]", "", "g"), ""),
        Integer,
    )
    async with AsyncSessionLocal() as session:
        filters = [
            Job.status != "sent",
            or_(
                Job.is_relevant.is_(None),
                Job.status == "new",
                and_(
                    Job.is_relevant.is_(True),
                    Job.status == "scored",
                    ~Job.id.in_(select(Proposal.job_id)),
                ),
            ),
        ]
        if is_kwork_paused():
            filters.append(Job.platform != KWORK_PLATFORM)
        result = await session.execute(
            select(Job.id)
            .where(*filters)
            .order_by(desc(kwork_project_id).nullslast(), Job.created_at.desc())
        )
        job_ids = [str(row[0]) for row in result.all() if str(row[0]) not in exclude]

    for job_id in job_ids:
        process_single_job.delay(job_id)

    if job_ids:
        logger.info("JobPilot AI queued unprocessed jobs", count=len(job_ids))
    return len(job_ids)


async def _scrape_and_process() -> dict:
    settings = get_settings()
    pause_refresh: dict[str, Any] = {}
    if settings.kwork_pause_enabled and settings.kwork_pause_auto:
        try:
            pause_refresh = await refresh_kwork_pause_from_kwork()
        except Exception as exc:
            logger.warning("Kwork pause refresh failed", error=str(exc))

    pause_reason = get_kwork_pause_reason()
    if pause_reason:
        logger.info("JobPilot AI Kwork pipeline paused", reason=pause_reason)

    if settings.telegram_bot_token and settings.telegram_admin_chat_id:
        bot = create_telegram_bot(settings.telegram_bot_token, settings)
        try:
            await maybe_notify_ollama_usage_warning(settings, bot)
        except Exception as exc:
            logger.warning("Ollama usage alert failed", error=str(exc))
        finally:
            await bot.session.close()

    scrapers = get_scrapers()
    if is_kwork_paused():
        scrapers = [scraper for scraper in scrapers if scraper.platform != KWORK_PLATFORM]
        if not scrapers:
            queued = await _queue_unprocessed_jobs(exclude=set())
            logger.info(
                "JobPilot AI scrape skipped: only Kwork enabled and paused",
                queued_reprocess=queued,
                pause_reason=pause_reason,
            )
            return {
                "processed": 0,
                "notified": 0,
                "scraped": 0,
                "queued_reprocess": queued,
                "kwork_paused": True,
                "pause_reason": pause_reason,
            }
    pipeline = JobPipelineService()
    graph = compile_jobpilot_graph()
    processed = 0
    notified = 0
    scraped_ids: set[str] = set()

    for scraper in scrapers:
        jobs = await scraper.scrape()
        for normalized in jobs:
            if is_kwork_paused() and normalized.platform == KWORK_PLATFORM:
                continue
            job_id = await pipeline.save_normalized_job(normalized)
            scraped_ids.add(str(job_id))
            if await pipeline.is_job_sent(job_id):
                continue
            state_data = await pipeline.job_to_state(job_id)
            result = await graph.ainvoke(state_data)
            proposal_id = await pipeline.persist_pipeline_results(job_id, result)

            if not result.get("is_relevant") or not proposal_id:
                continue

            processed += 1
            settings = get_settings()
            if settings.telegram_bot_token and settings.telegram_admin_chat_id:
                bot = create_telegram_bot(settings.telegram_bot_token, settings)
                try:
                    await notify_new_proposal(bot, job_id, proposal_id)
                    notified += 1
                finally:
                    await bot.session.close()

    queued = await _queue_unprocessed_jobs(exclude=scraped_ids)
    logger.info(
        "JobPilot AI scrape complete",
        processed=processed,
        notified=notified,
        scraped=len(scraped_ids),
        queued_reprocess=queued,
    )
    return {
        "processed": processed,
        "notified": notified,
        "scraped": len(scraped_ids),
        "queued_reprocess": queued,
        "pause_refresh": pause_refresh,
    }


@celery_app.task(name="app.tasks.scrape_tasks.refresh_kwork_pause")
def refresh_kwork_pause_task() -> dict:
    return _run_async(refresh_kwork_pause_from_kwork(force=True))


@celery_app.task(name="app.tasks.scrape_tasks.process_single_job")
def process_single_job(job_id: str) -> dict:
    return _run_async(_process_job(uuid.UUID(job_id)))


@celery_app.task(name="app.tasks.scrape_tasks.reprocess_pending_jobs")
def reprocess_pending_jobs() -> dict:
    return _run_async(_reprocess_pending())


async def _reprocess_pending() -> dict:
    queued = await _queue_unprocessed_jobs()
    return {"queued": queued}


async def _process_job(job_id: uuid.UUID) -> dict:
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal

    pipeline = JobPipelineService()
    if await pipeline.is_job_sent(job_id):
        return {"job_id": str(job_id), "skipped": "already_sent"}

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
    if job and job.platform == KWORK_PLATFORM and is_kwork_paused():
        reason = get_kwork_pause_reason()
        logger.info("JobPilot AI skip Kwork job while paused", job_id=str(job_id), reason=reason)
        return {"job_id": str(job_id), "skipped": "kwork_paused", "reason": reason}

    graph = compile_jobpilot_graph()
    state_data = await pipeline.job_to_state(job_id)
    result = await graph.ainvoke(state_data)
    proposal_id = await pipeline.persist_pipeline_results(job_id, result)

    if proposal_id:
        settings = get_settings()
        if settings.telegram_bot_token:
            bot = create_telegram_bot(settings.telegram_bot_token, settings)
            try:
                await notify_new_proposal(bot, job_id, proposal_id)
            finally:
                await bot.session.close()

    return {"job_id": str(job_id), "result": result}
