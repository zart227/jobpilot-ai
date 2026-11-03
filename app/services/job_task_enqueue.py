"""Deduplicate Celery enqueue for process_single_job (beat vs broker backlog)."""

from __future__ import annotations

import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)

_PENDING_KEY = "jobpilot:enqueue:process_job:{job_id}"


def _pending_key(job_id: str) -> str:
    return _PENDING_KEY.format(job_id=job_id)


def enqueue_dedup_ttl_seconds(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    lease = settings.job_processing_lease_seconds
    return max(lease * 2, 300)


def _redis_client(settings: Settings):
    import redis

    return redis.from_url(settings.celery_broker_url, decode_responses=True)


def try_reserve_process_job_enqueue(job_id: str, settings: Settings | None = None) -> bool:
    """Return True if this job was not already marked pending in the broker/worker pipeline."""
    settings = settings or get_settings()
    client = _redis_client(settings)
    try:
        reserved = client.set(
            _pending_key(job_id),
            "1",
            nx=True,
            ex=enqueue_dedup_ttl_seconds(settings),
        )
        return bool(reserved)
    except Exception as exc:
        logger.warning(
            "Job enqueue dedup unavailable; allowing enqueue",
            job_id=job_id,
            error=str(exc),
        )
        return True
    finally:
        client.close()


def release_process_job_enqueue(job_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    client = _redis_client(settings)
    try:
        client.delete(_pending_key(job_id))
    except Exception as exc:
        logger.warning("Failed to release job enqueue dedup marker", job_id=job_id, error=str(exc))
    finally:
        client.close()


def queue_process_single_job(job_id: str) -> str | None:
    """Enqueue process_single_job unless an identical task is already pending.

    Returns Celery task id when enqueued, None when skipped as duplicate.
    """
    from app.tasks.scrape_tasks import process_single_job

    if not try_reserve_process_job_enqueue(job_id):
        logger.debug("Skip duplicate process_single_job enqueue", job_id=job_id)
        return None
    try:
        return process_single_job.delay(job_id).id
    except Exception:
        release_process_job_enqueue(job_id)
        raise
