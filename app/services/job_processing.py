"""Job pipeline processing claims (short DB leases, no LLM inside transaction)."""

from __future__ import annotations

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
    now = datetime.now(UTC)
    claim_token = uuid.uuid4()
    now + timedelta(seconds=settings.job_processing_lease_seconds)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Job).where(Job.id == job_id).with_for_update())
        job = result.scalar_one_or_none()
        if not job:
            return None
        if not job_allows_pipeline_persist(job.status):
            return None
        if job.processing_next_attempt_at and job.processing_next_attempt_at > now:
            return None
        if has_active_processing_claim(job, settings):
            return None

        job.processing_claim_owner = owner
        job.processing_claim_token = claim_token
        job.processing_claimed_at = now
        job.processing_stage = "pipeline"
        job.processing_attempt_count = (job.processing_attempt_count or 0) + 1
        await session.commit()
        return JobClaim(job_id=job.id, claim_token=claim_token)


async def release_job_claim(
    job_id: uuid.UUID,
    claim_token: uuid.UUID,
    *,
    stage: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job or job.processing_claim_token != claim_token:
            return
        job.processing_claim_owner = None
        job.processing_claim_token = None
        job.processing_claimed_at = None
        if stage:
            job.processing_stage = stage
        await session.commit()


async def record_processing_error(
    job_id: uuid.UUID,
    claim_token: uuid.UUID,
    *,
    stage: str,
    error: str,
    retryable: bool,
) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job or job.processing_claim_token != claim_token:
            return
        job.processing_stage = stage
        job.processing_error_type = "retryable" if retryable else "permanent"
        job.status = JOB_STATUS_RETRYABLE_ERROR if retryable else JOB_STATUS_PERMANENT_ERROR
        job.processing_claim_owner = None
        job.processing_claim_token = None
        job.processing_claimed_at = None
        if retryable:
            delay = compute_backoff_seconds(
                job.processing_attempt_count or 1,
                settings.job_processing_backoff_seconds,
            )
            job.processing_next_attempt_at = now + timedelta(seconds=delay)
        else:
            job.processing_next_attempt_at = None
        await session.commit()


async def recover_stale_job_claims(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    recovered = 0
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Job).where(Job.processing_claim_token.is_not(None)))
        for job in result.scalars():
            expires = _lease_expires(job, settings)
            if expires and expires < now:
                job.processing_claim_owner = None
                job.processing_claim_token = None
                job.processing_claimed_at = None
                recovered += 1
        if recovered:
            await session.commit()
    return recovered
