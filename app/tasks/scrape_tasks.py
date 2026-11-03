import os
import uuid
from typing import Any

import structlog

from app.agents.graph import compile_jobpilot_graph
from app.celery_app import celery_app
from app.config import get_settings
from app.llm.ollama_usage import maybe_notify_ollama_usage_warning
from app.llm.openai_usage import maybe_notify_openai_usage_warning
from app.scrapers.registry import get_scrapers
from app.services.job_pipeline import JobPipelineService
from app.services.job_processing import (
    record_processing_error,
    recover_stale_job_claims,
    release_job_claim,
    run_job_graph_with_claim,
    try_claim_job,
)
from app.services.job_task_enqueue import queue_process_single_job, release_process_job_enqueue
from app.services.kwork_browser import refresh_kwork_pause_from_kwork
from app.services.kwork_inbox import check_kwork_inbox as run_kwork_inbox_check
from app.services.kwork_pause import (
    KWORK_PLATFORM,
    get_kwork_pause_reason,
    is_kwork_inbox_paused,
    is_kwork_offers_paused,
)
from app.services.notification_outbox import deliver_notification_outbox
from app.tasks.async_runner import run_async as _run_async
from app.telegram.bot import notify_new_proposal
from app.utils.proxy import create_telegram_bot

logger = structlog.get_logger(__name__)


@celery_app.task(name="app.tasks.scrape_tasks.run_all_scrapers", bind=True, max_retries=3)
def run_all_scrapers(self) -> dict:
    logger.info("JobPilot AI scrape task started")
    try:
        return _run_async(_scrape_and_process())
    except Exception as exc:
        logger.error("Scrape task failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)


async def _queue_unprocessed_jobs(exclude: set[str] | None = None) -> int:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import BigInteger, and_, case, cast, desc, func, or_, select

    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal

    exclude = exclude or set()
    project_digits = func.nullif(func.regexp_replace(Job.external_id, r"[^0-9]", "", "g"), "")
    kwork_project_id = case(
        (and_(Job.platform == KWORK_PLATFORM, func.length(project_digits) <= 18),
         cast(project_digits, BigInteger)),
        else_=None,
    )
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=get_settings().job_processing_lease_seconds)
    async with AsyncSessionLocal() as session:
        filters = [
            or_(Job.processing_next_attempt_at.is_(None), Job.processing_next_attempt_at <= now),
            or_(Job.processing_claim_token.is_(None), Job.processing_claimed_at.is_(None),
                Job.processing_claimed_at <= stale_before),
            or_(
                Job.status.in_(("new", "retryable_error")),
                and_(Job.is_relevant.is_(True), Job.status == "scored",
                     ~Job.id.in_(select(Proposal.job_id))),
            ),
        ]
        if is_kwork_offers_paused():
            filters.append(Job.platform != KWORK_PLATFORM)
        result = await session.execute(
            select(Job.id)
            .where(*filters)
            .order_by(desc(kwork_project_id).nullslast(), Job.created_at.desc())
        )
        job_ids = [str(row[0]) for row in result.all() if str(row[0]) not in exclude]

    queued = 0
    for job_id in job_ids:
        if queue_process_single_job(job_id) is not None:
            queued += 1

    if queued:
        logger.info("JobPilot AI queued unprocessed jobs", count=queued, candidates=len(job_ids))
    return queued


async def _refresh_kwork_pause_state(*, force: bool = True) -> dict[str, Any]:
    settings = get_settings()
    if not settings.kwork_pause_enabled or not settings.kwork_pause_auto:
        return {"skipped": True, "reason": "auto_pause_disabled"}
    try:
        return await refresh_kwork_pause_from_kwork(force=force)
    except Exception as exc:
        logger.warning("Kwork pause refresh failed", error=str(exc))
        return {"error": str(exc)}


async def _scrape_and_process() -> dict:
    settings = get_settings()
    await recover_stale_job_claims()
    if is_kwork_offers_paused():
        pause_refresh: dict[str, Any] = {"skipped": True, "reason": "already_paused"}
    else:
        pause_refresh = await _refresh_kwork_pause_state(force=True)

    pause_reason = get_kwork_pause_reason() if is_kwork_offers_paused() else None
    if is_kwork_offers_paused():
        logger.info(
            "JobPilot AI Kwork offers paused (scrape/pipeline)",
            reason=pause_reason or pause_refresh,
        )

    if settings.telegram_bot_token and settings.telegram_admin_chat_id:
        bot = create_telegram_bot(settings.telegram_bot_token, settings)
        try:
            try:
                await maybe_notify_ollama_usage_warning(settings, bot)
            except Exception as exc:
                logger.warning("Ollama usage alert failed", error=str(exc))
            try:
                await maybe_notify_openai_usage_warning(settings, bot)
            except Exception as exc:
                logger.warning("OpenAI usage alert failed", error=str(exc))
            if not is_kwork_inbox_paused():
                try:
                    await run_kwork_inbox_check(settings, bot=bot)
                except Exception as exc:
                    logger.warning("Kwork inbox check failed", error=str(exc))
            else:
                logger.info("JobPilot AI skip Kwork inbox while inbox pause active")
        finally:
            await bot.session.close()

    scrapers = get_scrapers()
    pipeline = JobPipelineService()
    graph = compile_jobpilot_graph()
    processed = 0
    notified = 0
    failed_sources = 0
    failed_jobs = 0
    scraped_ids: set[str] = set()

    for scraper in scrapers:
        try:
            jobs = await scraper.scrape()
        except Exception as exc:
            failed_sources += 1
            logger.error("Scraper failed", scraper=type(scraper).__name__, error=str(exc))
            continue
        for normalized in jobs:
            if normalized.platform == KWORK_PLATFORM and is_kwork_offers_paused():
                continue
            try:
                job_id = await pipeline.save_normalized_job(normalized)
                scraped_ids.add(str(job_id))
                if await pipeline.is_job_sent(job_id):
                    continue
                claim = await try_claim_job(job_id, owner="scrape_task")
            except Exception as exc:
                failed_jobs += 1
                logger.error(
                    "Failed to save or claim scraped job",
                    platform=normalized.platform,
                    external_id=normalized.external_id,
                    error=str(exc),
                )
                continue
            if not claim:
                continue
            job_failed = False
            try:
                from app.db.session import AsyncSessionLocal
                from app.services.job_freshness import skip_if_stale

                async with AsyncSessionLocal() as session:
                    stale_reason = await skip_if_stale(session, job_id)
                    if stale_reason:
                        await session.commit()
                        await pipeline.persist_pipeline_results(
                            job_id,
                            {
                                "is_relevant": False,
                                "relevance_reason": stale_reason,
                                "filter_source": "job_freshness",
                            },
                            claim_token=claim.claim_token,
                        )
                        continue

                state_data = await pipeline.job_to_state(job_id)
                result = await run_job_graph_with_claim(graph, state_data, claim)
                proposal_id = await pipeline.persist_pipeline_results(
                    job_id, result, claim_token=claim.claim_token
                )

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
            except Exception as exc:
                failed_jobs += 1
                job_failed = True
                logger.error(
                    "JobPilot AI pipeline failed for scraped job",
                    job_id=str(job_id),
                    error=str(exc),
                )
                try:
                    await record_processing_error(
                        claim.job_id, claim.claim_token, stage="pipeline", error=str(exc), retryable=True,
                    )
                except Exception:
                    logger.exception("Failed to record scraped job processing error", job_id=str(job_id))
            finally:
                try:
                    await release_job_claim(claim.job_id, claim.claim_token)
                except Exception as exc:
                    if not job_failed:
                        failed_jobs += 1
                    logger.error("Failed to release job claim", job_id=str(job_id), error=str(exc))

    queued = await _queue_unprocessed_jobs(exclude=scraped_ids)
    logger.info(
        "JobPilot AI scrape complete",
        processed=processed,
        notified=notified,
        scraped=len(scraped_ids),
        queued_reprocess=queued,
        failed_sources=failed_sources,
        failed_jobs=failed_jobs,
    )
    return {
        "processed": processed,
        "notified": notified,
        "scraped": len(scraped_ids),
        "queued_reprocess": queued,
        "failed_sources": failed_sources,
        "failed_jobs": failed_jobs,
        "pause_refresh": pause_refresh,
        "kwork_offers_paused": is_kwork_offers_paused(),
        "pause_reason": pause_reason,
    }


@celery_app.task(name="app.tasks.scrape_tasks.refresh_kwork_pause")
def refresh_kwork_pause_task() -> dict:
    return _run_async(refresh_kwork_pause_from_kwork(force=True))


@celery_app.task(name="app.tasks.scrape_tasks.deliver_notification_outbox")
def deliver_notification_outbox_task() -> dict:
    return _run_async(deliver_notification_outbox())


@celery_app.task(name="app.tasks.scrape_tasks.check_kwork_inbox")
def check_kwork_inbox_task() -> dict:
    if is_kwork_inbox_paused():
        reason = get_kwork_pause_reason()
        logger.info("JobPilot AI skip Kwork inbox while paused", reason=reason)
        return {"skipped": True, "kwork_inbox_paused": True, "pause_reason": reason}
    return _run_async(run_kwork_inbox_check())


@celery_app.task(name="app.tasks.scrape_tasks.process_single_job")
def process_single_job(job_id: str) -> dict:
    try:
        return _run_async(_process_job(uuid.UUID(job_id)))
    finally:
        release_process_job_enqueue(job_id)


@celery_app.task(name="app.tasks.scrape_tasks.reprocess_pending_jobs")
def reprocess_pending_jobs() -> dict:
    return _run_async(_reprocess_pending())


async def _reprocess_pending() -> dict:
    if is_kwork_offers_paused() and get_settings().enabled_scrapers.strip() == "kwork":
        reason = get_kwork_pause_reason()
        logger.info("JobPilot AI skip reprocess while Kwork paused", reason=reason)
        return {"queued": 0, "kwork_paused": True, "pause_reason": reason}
    recovered = await recover_stale_job_claims()
    queued = await _queue_unprocessed_jobs()
    return {"queued": queued, "stale_claims_recovered": recovered}


async def _process_job(job_id: uuid.UUID) -> dict:
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal

    pipeline = JobPipelineService()
    if await pipeline.is_job_sent(job_id):
        return {"job_id": str(job_id), "skipped": "already_sent"}

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
    if job and job.platform == KWORK_PLATFORM and is_kwork_offers_paused():
        reason = get_kwork_pause_reason()
        logger.info("JobPilot AI skip Kwork job while paused", job_id=str(job_id), reason=reason)
        return {"job_id": str(job_id), "skipped": "kwork_paused", "reason": reason}

    claim = await try_claim_job(job_id, owner="process_single_job")
    if not claim:
        return {"job_id": str(job_id), "skipped": "processing_claim_active"}

    settings = get_settings()
    hold = settings.job_processing_c052_drill_hold_seconds
    if (
        hold > 0
        and os.environ.get("JOBPILOT_C052_COMPOSE_DRILL") == "1"
    ):
        async with AsyncSessionLocal() as session:
            held_job = await session.get(Job, job_id)
        if held_job and held_job.platform == "fake":
            import asyncio

            logger.info("C052 drill hold after claim", job_id=str(job_id), seconds=hold)
            await asyncio.sleep(hold)

    try:
        graph = compile_jobpilot_graph()
        from app.services.job_freshness import skip_if_stale

        async with AsyncSessionLocal() as session:
            stale_reason = await skip_if_stale(session, job_id)
            if stale_reason:
                await session.commit()
                await pipeline.persist_pipeline_results(
                    job_id,
                    {
                        "is_relevant": False,
                        "relevance_reason": stale_reason,
                        "filter_source": "job_freshness",
                    },
                    claim_token=claim.claim_token,
                )
                return {"job_id": str(job_id), "skipped": "stale", "reason": stale_reason}

        state_data = await pipeline.job_to_state(job_id)
        result = await run_job_graph_with_claim(graph, state_data, claim)
        proposal_id = await pipeline.persist_pipeline_results(
            job_id, result, claim_token=claim.claim_token
        )

        if proposal_id:
            settings = get_settings()
            if settings.telegram_bot_token:
                bot = create_telegram_bot(settings.telegram_bot_token, settings)
                try:
                    await notify_new_proposal(bot, job_id, proposal_id)
                finally:
                    await bot.session.close()

        return {"job_id": str(job_id), "result": result}
    except Exception as exc:
        try:
            await record_processing_error(
                claim.job_id, claim.claim_token, stage="pipeline", error=str(exc), retryable=True,
            )
        except Exception:
            logger.exception("Failed to record job processing error", job_id=str(job_id))
        raise
    finally:
        await release_job_claim(claim.job_id, claim.claim_token)
