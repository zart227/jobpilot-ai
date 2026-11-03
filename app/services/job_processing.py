"""Job pipeline processing claims (short DB leases, no LLM inside transaction)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db.models import Job
from app.db.session import AsyncSessionLocal
from app.db.workflow_states import (
    JOB_STATUS_PERMANENT_ERROR,
    JOB_STATUS_RETRYABLE_ERROR,
    job_allows_pipeline_persist,
)
from app.services.notification_outbox import compute_backoff_seconds

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class JobClaim:
    job_id: uuid.UUID
    claim_token: uuid.UUID
    attempt_id: uuid.UUID


def _append_processing_journal(
    job: Job,
    event: str,
    *,
    stage: str | None = None,
    claim_token: uuid.UUID | None = None,
    owner: str | None = None,
    error: str | None = None,
    attempt_id: uuid.UUID | None = None,
) -> None:
    raw = dict(job.raw_data or {})
    if attempt_id is not None:
        raw["current_processing_attempt_id"] = str(attempt_id)
    entry: dict[str, object] = {
        "at": datetime.now(UTC).isoformat(),
        "event": event,
        "processing_attempt": job.processing_attempt_count,
        "stage": stage if stage is not None else job.processing_stage,
    }
    if claim_token is not None:
        entry["claim_token"] = str(claim_token)
    if owner is not None:
        entry["owner"] = owner
    if error is not None:
        entry["error"] = error[:500]
    if attempt_id is not None:
        entry["attempt_id"] = str(attempt_id)
    journal = list(raw.get("processing_attempt_journal") or [])
    journal.append(entry)
    raw["processing_attempt_journal"] = journal[-100:]
    job.raw_data = raw


def _lease_expires(job: Job, settings: Settings) -> datetime | None:
    if not job.processing_claimed_at:
        return None
    return job.processing_claimed_at + timedelta(seconds=settings.job_processing_lease_seconds)


def has_active_processing_claim(job: Job, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not job.processing_claim_token:
        return False
    expires = _lease_expires(job, settings)
    if expires is None:
        return False
    return datetime.now(UTC) < expires


async def try_claim_job(job_id: uuid.UUID, owner: str) -> JobClaim | None:
    settings = get_settings()
    claim_token = uuid.uuid4()
    attempt_id = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Job).where(Job.id == job_id).with_for_update())
        job = result.scalar_one_or_none()
        if not job:
            return None
        if not job_allows_pipeline_persist(job.status):
            return None
        now = datetime.now(UTC)
        if job.processing_next_attempt_at and job.processing_next_attempt_at > now:
            return None
        if has_active_processing_claim(job, settings):
            return None
        if (job.processing_attempt_count or 0) >= settings.job_processing_max_attempts:
            job.status = JOB_STATUS_PERMANENT_ERROR
            job.processing_error_type = "retry_exhausted"
            job.processing_next_attempt_at = None
            job.processing_claim_owner = None
            job.processing_claim_token = None
            job.processing_claimed_at = None
            await session.commit()
            return None

        job.processing_claim_owner = owner
        job.processing_claim_token = claim_token
        job.processing_claimed_at = now
        job.processing_stage = "pipeline"
        job.processing_attempt_count = (job.processing_attempt_count or 0) + 1
        _append_processing_journal(
            job,
            "claimed",
            stage="pipeline",
            claim_token=claim_token,
            owner=owner,
            attempt_id=attempt_id,
        )
        await session.commit()
        logger.info(
            "Job processing claimed",
            job_id=str(job_id),
            processing_attempt=job.processing_attempt_count,
            claim_token=str(claim_token),
            attempt_id=str(attempt_id),
            owner=owner,
            stage="pipeline",
        )
        return JobClaim(job_id=job.id, claim_token=claim_token, attempt_id=attempt_id)


class JobClaimLost(RuntimeError):
    """A pipeline worker lost its right to persist this job."""


async def renew_job_claim(claim: JobClaim) -> bool:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        job = await session.scalar(select(Job).where(Job.id == claim.job_id).with_for_update())
        if (not job or job.processing_claim_token != claim.claim_token
                or not job_allows_pipeline_persist(job.status)
                or not has_active_processing_claim(job, settings)):
            return False
        job.processing_claimed_at = datetime.now(UTC)
        await session.commit()
        return True


async def _job_claim_heartbeat(claim: JobClaim) -> None:
    while True:
        await asyncio.sleep(max(0.05, get_settings().job_processing_lease_seconds / 3))
        if not await renew_job_claim(claim):
            raise JobClaimLost("job processing claim lost")


async def run_job_graph_with_claim(graph, state, claim: JobClaim):
    """Keep a claim alive during graph execution without holding a database transaction."""
    if not await renew_job_claim(claim):
        raise JobClaimLost("job processing claim expired before graph execution")
    graph_task = asyncio.create_task(graph.ainvoke(state))
    heartbeat = asyncio.create_task(_job_claim_heartbeat(claim))
    try:
        done, _ = await asyncio.wait((graph_task, heartbeat), return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done:
            await heartbeat
            raise JobClaimLost("job processing heartbeat stopped")
        return await graph_task
    finally:
        for task in (graph_task, heartbeat):
            if not task.done():
                task.cancel()
        await asyncio.gather(graph_task, heartbeat, return_exceptions=True)


async def release_job_claim(
    job_id: uuid.UUID,
    claim_token: uuid.UUID,
    *,
    stage: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.processing_claim_token != claim_token:
            return
        _append_processing_journal(
            job,
            "released",
            stage=stage,
            claim_token=claim_token,
        )
        job.processing_claim_owner = None
        job.processing_claim_token = None
        job.processing_claimed_at = None
        if stage:
            job.processing_stage = stage
        await session.commit()


def apply_processing_failure(job: Job, *, stage: str, retryable: bool, settings: Settings) -> None:
    exhausted = retryable and (job.processing_attempt_count or 0) >= settings.job_processing_max_attempts
    job.processing_stage = stage
    job.processing_error_type = "retry_exhausted" if exhausted else ("retryable" if retryable else "permanent")
    job.status = JOB_STATUS_RETRYABLE_ERROR if retryable and not exhausted else JOB_STATUS_PERMANENT_ERROR
    job.processing_claim_owner = None
    job.processing_claim_token = None
    job.processing_claimed_at = None
    if retryable and not exhausted:
        delay = compute_backoff_seconds(job.processing_attempt_count or 1, settings.job_processing_backoff_seconds)
        job.processing_next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
    else:
        job.processing_next_attempt_at = None


async def record_processing_error(
    job_id: uuid.UUID, claim_token: uuid.UUID, *, stage: str, error: str, retryable: bool,
) -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if (not job or job.processing_claim_token != claim_token
                or not has_active_processing_claim(job, settings)):
            return
        if not job_allows_pipeline_persist(job.status):
            return
        apply_processing_failure(job, stage=stage, retryable=retryable, settings=settings)
        _append_processing_journal(
            job,
            "processing_error",
            stage=stage,
            claim_token=claim_token,
            error=error,
        )
        logger.info(
            "Job processing error recorded",
            job_id=str(job_id),
            processing_attempt=job.processing_attempt_count,
            claim_token=str(claim_token),
            stage=stage,
            retryable=retryable,
            status=job.status,
        )
        await session.commit()


async def recover_stale_job_claims(settings: Settings | None = None) -> int:
    from app.services.job_task_enqueue import release_process_job_enqueue

    settings = settings or get_settings()
    now = datetime.now(UTC)
    recovered = 0
    recovered_ids: list[uuid.UUID] = []
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Job)
            .where(Job.processing_claim_token.is_not(None))
            .with_for_update(skip_locked=True)
        )
        for job in result.scalars():
            expires = _lease_expires(job, settings)
            if expires and expires < now:
                _append_processing_journal(
                    job,
                    "stale_claim_recovered",
                    claim_token=job.processing_claim_token,
                    owner=job.processing_claim_owner,
                    stage=job.processing_stage,
                )
                job.processing_claim_owner = None
                job.processing_claim_token = None
                job.processing_claimed_at = None
                recovered += 1
                recovered_ids.append(job.id)
        if recovered:
            await session.commit()
    for job_id in recovered_ids:
        release_process_job_enqueue(str(job_id))
        logger.info(
            "Recovered stale job processing claim",
            job_id=str(job_id),
        )
    return recovered


async def requeue_exhausted_job(session, job_id: uuid.UUID) -> bool:
    """Explicitly retry one exhausted job that has no prior delivery artifacts."""
    from app.db.models import Proposal, SendAttempt

    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if (not job or job.status != JOB_STATUS_PERMANENT_ERROR
            or job.processing_error_type != "retry_exhausted" or job.processing_claim_token):
        return False
    if await session.scalar(select(Proposal.id).where(Proposal.job_id == job_id).limit(1)):
        return False
    if await session.scalar(select(SendAttempt.id).where(SendAttempt.job_id == job_id).limit(1)):
        return False
    previous_attempts = job.processing_attempt_count or 0
    job.raw_data = {
        **(job.raw_data or {}),
        "operator_retry_requeue": {
            "at": datetime.now(UTC).isoformat(), "previous_attempts": previous_attempts,
        },
    }
    job.status = JOB_STATUS_RETRYABLE_ERROR
    job.processing_error_type = "retryable"
    job.processing_attempt_count = 0
    job.processing_next_attempt_at = None
    job.processing_claim_owner = None
    job.processing_claimed_at = None
    job.processing_stage = None
    await session.flush()
    return True
