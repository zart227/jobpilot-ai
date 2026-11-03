"""Reassessment must fence changes and never reuse an old human approval."""
import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from test_kwork_opportunity_send_gate import seed

from app.config import Settings
from app.db.models import Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.services.kwork_reassessment import reassess_kwork_job
from app.services.opportunity_policy import kwork_opportunity_fingerprint

pytestmark = pytest.mark.skipif(
    os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required'
)


def approved_result(state):
    return {'is_relevant': True, 'opportunity_assessment': {
        'relevant': True, 'assessment_id': str(uuid.uuid4()),
        'source_fingerprint': kwork_opportunity_fingerprint(state['job_data']),
        'estimated_hours': 2, 'estimated_calendar_days': 1, 'scope_clear': True,
        'complexity': 'simple', 'minimum_price_rub': 700,
    }}


async def fresh_seed():
    job_id, proposal_id, client_id, pending_id = await seed(valid=True)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.raw_data = {**job.raw_data, 'listing_observed_at': datetime.now(UTC).isoformat()}
        await session.commit()
    return job_id, proposal_id, client_id, pending_id


async def test_reassessment_requires_new_review_even_if_price_is_unchanged():
    from app.services.review_reissue import reissue_review
    from app.services.send_attempt_service import prepare_proposal_send

    job_id, proposal_id, _, pending_id = await fresh_seed()
    evaluate = AsyncMock(side_effect=approved_result)
    result = await reassess_kwork_job(job_id, evaluator=evaluate)
    assert result['status'] == 'accepted'
    claim, _, _ = await prepare_proposal_send(str(job_id), str(proposal_id), 'Approved')
    assert (claim.action, claim.reason) == ('blocked', 'durable_approval_required')
    cfg = Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID='100', TELEGRAM_ADMIN_USER_ID='5')
    review = await reissue_review('proposal', pending_id, chat_id=100, user_id=5, settings=cfg)
    assert review.action == 'queued' and review.pending_id != pending_id
    async with AsyncSessionLocal() as session:
        assert (await session.get(Proposal, proposal_id)).status == 'pending_approval'
        assert (await session.get(TelegramPending, review.pending_id)).status == 'pending'


async def test_stale_source_never_calls_llm():
    job_id, _, _, _ = await fresh_seed()
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        job.raw_data = {**job.raw_data, 'listing_observed_at': (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
        before = dict(job.raw_data)
        await session.commit()
    evaluate = AsyncMock(side_effect=approved_result)
    assert await reassess_kwork_job(job_id, evaluator=evaluate) == {'status': 'blocked', 'reason': 'source_refresh_required'}
    evaluate.assert_not_awaited()
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).raw_data == before


async def test_changes_during_evaluation_discard_result():
    job_id, _, _, _ = await fresh_seed()
    async def evaluate(state):
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            job.description = 'Changed while LLM was processing'
            await session.commit()
        return approved_result(state)
    assert await reassess_kwork_job(job_id, evaluator=evaluate) == {'status': 'blocked', 'reason': 'changed_during_evaluation'}


async def test_failed_or_invalid_llm_does_not_change_assessment():
    job_id, _, _, _ = await fresh_seed()
    async with AsyncSessionLocal() as session:
        before = (await session.get(Job, job_id)).raw_data
    result = await reassess_kwork_job(job_id, evaluator=AsyncMock(return_value={'pipeline_processing_error': True}))
    assert result['reason'] == 'evaluation_failed'
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, job_id)).raw_data == before


async def test_concurrent_evaluations_only_one_can_commit():
    job_id, _, _, _ = await fresh_seed()
    entered = 0
    ready = asyncio.Event()
    async def evaluate(state):
        nonlocal entered
        entered += 1
        if entered == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 5)
        return approved_result(state)
    first, second = await asyncio.gather(reassess_kwork_job(job_id, evaluator=evaluate),
                                         reassess_kwork_job(job_id, evaluator=evaluate))
    assert sorted([first['status'], second['status']]) == ['accepted', 'blocked']
    blocked = first if first['status'] == 'blocked' else second
    assert blocked['reason'] == 'changed_during_evaluation'


async def test_rejection_revokes_old_approval():
    from app.services.send_attempt_service import prepare_proposal_send
    job_id, proposal_id, _, _ = await fresh_seed()
    result = await reassess_kwork_job(job_id, evaluator=AsyncMock(return_value={
        'is_relevant': False, 'relevance_reason': 'too complex',
    }))
    assert result['status'] == 'rejected'
    claim, _, _ = await prepare_proposal_send(str(job_id), str(proposal_id), 'Approved')
    assert (claim.action, claim.reason) == ('blocked', 'assessment_required')


async def test_reused_assessment_revision_is_rejected():
    job_id, _, _, _ = await fresh_seed()
    async with AsyncSessionLocal() as session:
        old_id = (await session.get(Job, job_id)).raw_data['opportunity_assessment']['assessment_id']
    def reused(state):
        result = approved_result(state)
        result['opportunity_assessment']['assessment_id'] = old_id
        return result
    result = await reassess_kwork_job(job_id, evaluator=AsyncMock(side_effect=reused))
    assert result == {'status': 'blocked', 'reason': 'assessment_revision_reused'}


async def test_changed_source_during_evaluation_discards_result_even_if_facts_match():
    job_id, _, _, _ = await fresh_seed()
    async def evaluate(state):
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            job.raw_data = {**job.raw_data, 'listing_observed_at': datetime.now(UTC).isoformat()}
            await session.commit()
        return approved_result(state)
    result = await reassess_kwork_job(job_id, evaluator=evaluate)
    assert result == {'status': 'blocked', 'reason': 'changed_during_evaluation'}
