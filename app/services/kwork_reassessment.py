"""Explicit, fenced reassessment of one recently observed Kwork listing."""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from app.config import Settings, get_settings
from app.db.models import Job, Proposal
from app.db.session import AsyncSessionLocal
from app.services.job_freshness import append_decision_audit, stale_generation_reason
from app.services.opportunity_policy import (
    kwork_assessment_rejection,
    kwork_job_facts,
    kwork_opportunity_fingerprint,
)
from app.services.send_attempt_service import delivery_blocks_mutation

MAX_SOURCE_AGE = timedelta(minutes=15)


def _fresh_source(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        observed = datetime.fromisoformat(value)
        if observed.tzinfo is None:
            return False
        age = datetime.now(UTC) - observed.astimezone(UTC)
        return timedelta(0) <= age <= MAX_SOURCE_AGE
    except (TypeError, ValueError, OverflowError):
        return False


async def _block_reason(session, job: Job, settings: Settings) -> str | None:
    if job.platform != 'kwork':
        return 'not_kwork'
    if job.status in ('sent', 'sending') or job.processing_claim_token:
        return 'protected_or_processing'
    if stale_generation_reason(job, settings):
        return 'job_expired_or_stale'
    if not _fresh_source((job.raw_data or {}).get('listing_observed_at')):
        return 'source_refresh_required'
    sent = await session.scalar(select(Proposal.id).where(
        Proposal.job_id == job.id,
        or_(Proposal.status == 'sent', Proposal.sent_at.is_not(None)),
    ).limit(1))
    if sent or await delivery_blocks_mutation(session, job_id=job.id, include_confirmed=True):
        return 'delivery_requires_resolution'
    return None


async def reassess_kwork_job(
    job_id: uuid.UUID, *, settings: Settings | None = None,
    evaluator: Callable[[dict], Awaitable[dict]] | None = None,
) -> dict[str, str]:
    """Evaluate outside DB transactions; never approve, send, notify or create review cards."""
    settings = settings or get_settings()
    async with AsyncSessionLocal() as session:
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job:
            return {'status': 'blocked', 'reason': 'not_found'}
        reason = await _block_reason(session, job, settings)
        if reason:
            return {'status': 'blocked', 'reason': reason}
        facts = await kwork_job_facts(session, job)
        observed = (job.raw_data or {}).get('listing_observed_at')
        old_assessment = (job.raw_data or {}).get('opportunity_assessment')
        fingerprint = kwork_opportunity_fingerprint(facts)
    if evaluator is None:
        from app.agents.filter_agent import FilterAgent

        evaluator = FilterAgent().run
    result = await evaluator({'job_id': str(job_id), 'job_data': facts})
    if result.get('pipeline_processing_error'):
        return {'status': 'blocked', 'reason': 'evaluation_failed'}
    relevant = result.get('is_relevant')
    if type(relevant) is not bool:
        return {'status': 'blocked', 'reason': 'invalid_evaluation'}
    assessment = result.get('opportunity_assessment')
    if relevant:
        if kwork_assessment_rejection(facts, assessment):
            return {'status': 'blocked', 'reason': 'invalid_evaluation'}
    else:
        assessment = {'relevant': False, 'assessment_id': str(uuid.uuid4()),
                      'source_fingerprint': fingerprint,
                      'reason': str(result.get('relevance_reason') or 'not eligible')[:1000]}
    if isinstance(old_assessment, dict) and assessment.get('assessment_id') == old_assessment.get('assessment_id'):
        return {'status': 'blocked', 'reason': 'assessment_revision_reused'}
    async with AsyncSessionLocal() as session:
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job:
            return {'status': 'blocked', 'reason': 'not_found'}
        reason = await _block_reason(session, job, settings)
        if reason:
            return {'status': 'blocked', 'reason': reason}
        raw = job.raw_data or {}
        current = await kwork_job_facts(session, job)
        if (raw.get('listing_observed_at') != observed
            or raw.get('opportunity_assessment') != old_assessment
            or kwork_opportunity_fingerprint(current) != fingerprint):
            return {'status': 'blocked', 'reason': 'changed_during_evaluation'}
        job.raw_data = {**raw, 'opportunity_assessment': assessment}
        append_decision_audit(job, action='reassessed',
                              reason='accepted' if relevant else 'rejected', source='operator')
        await session.commit()
    return {'status': 'accepted' if relevant else 'rejected', 'assessment_id': assessment['assessment_id']}
