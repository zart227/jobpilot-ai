"""A committed proposal always has its immutable review and durable outbox."""
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from test_pipeline_claim_fencing import seed

from app.config import get_settings
from app.db.models import (
    Job,
    NotificationOutbox,
    Proposal,
    ProposalApprovalSnapshot,
    TelegramPending,
)
from app.db.session import AsyncSessionLocal
from app.services.job_pipeline import JobPipelineService

pytestmark = pytest.mark.skipif(
    os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required'
)


@pytest.fixture
def review_chat(monkeypatch):
    monkeypatch.setenv('TELEGRAM_ADMIN_CHAT_ID', '100')
    get_settings.cache_clear()


async def test_proposal_review_and_outbox_commit_together(review_chat):
    jid, token = await seed()
    proposal_id = await JobPipelineService().persist_pipeline_results(jid, {
        'score': 99, 'is_relevant': True, 'proposal_content': 'Text for a human review',
    }, claim_token=token)
    async with AsyncSessionLocal() as session:
        pending = await session.scalar(select(TelegramPending).where(TelegramPending.proposal_id == proposal_id))
        assert pending and (await session.get(ProposalApprovalSnapshot, pending.id)).payload['content'] == 'Text for a human review'
        outbox = await session.scalar(select(NotificationOutbox).where(NotificationOutbox.reference_id == pending.id))
        assert outbox and outbox.idempotency_key == f'proposal_approval:{proposal_id}:v1'
        assert outbox.payload['review_content'] == 'Text for a human review'
        assert (await session.get(Job, jid)).status == 'pending_approval'


async def test_outbox_error_rolls_back_entire_pipeline_commit(monkeypatch, review_chat):
    from app.telegram import bot

    jid, token = await seed()
    monkeypatch.setattr(bot, 'enqueue_notification', AsyncMock(side_effect=RuntimeError('outbox failure')))
    with pytest.raises(RuntimeError, match='outbox failure'):
        await JobPipelineService().persist_pipeline_results(jid, {
            'score': 99, 'is_relevant': True, 'proposal_content': 'Text for a human review',
        }, claim_token=token)
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, jid)).status == 'new'
        assert await session.scalar(select(func.count()).select_from(Proposal).where(Proposal.job_id == jid)) == 0
        assert await session.scalar(select(func.count()).select_from(TelegramPending).where(TelegramPending.job_id == jid)) == 0


async def test_missing_review_destination_does_not_commit_orphan(monkeypatch):
    monkeypatch.setenv('TELEGRAM_ADMIN_CHAT_ID', '')
    get_settings.cache_clear()
    jid, token = await seed()
    with pytest.raises(RuntimeError, match='destination'):
        await JobPipelineService().persist_pipeline_results(jid, {
            'score': 99, 'is_relevant': True, 'proposal_content': 'Text for a human review',
        }, claim_token=token)
    async with AsyncSessionLocal() as session:
        assert (await session.get(Job, jid)).status == 'new'
        assert await session.scalar(select(Proposal.id).where(Proposal.job_id == jid)) is None


async def test_queue_replay_uses_same_pending_and_no_duplicate(review_chat):
    from app.telegram.bot import queue_proposal_review

    jid, token = await seed()
    proposal_id = await JobPipelineService().persist_pipeline_results(jid, {
        'score': 99, 'is_relevant': True, 'proposal_content': 'Text for a human review',
    }, claim_token=token)
    async with AsyncSessionLocal() as session:
        job, proposal = await session.get(Job, jid), await session.get(Proposal, proposal_id)
        first = await queue_proposal_review(session, job, proposal, 100)
        second = await queue_proposal_review(session, job, proposal, 100)
        await session.commit()
        assert first == second
        assert await session.scalar(select(func.count()).select_from(TelegramPending).where(
            TelegramPending.proposal_id == proposal_id)) == 1


async def test_legacy_card_without_outbox_is_not_silently_reissued(review_chat):
    from app.telegram.bot import queue_proposal_review

    jid, _token = await seed()
    proposal_id, pending_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Proposal(id=proposal_id, job_id=jid, content='Original text', status='pending_approval', version=1))
        await session.flush()
        session.add(TelegramPending(id=pending_id, job_id=jid, proposal_id=proposal_id,
                                    chat_id=100, status='pending'))
        await session.commit()
    async with AsyncSessionLocal() as session:
        job, proposal = await session.get(Job, jid), await session.get(Proposal, proposal_id)
        assert await queue_proposal_review(session, job, proposal, 100) == pending_id
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(NotificationOutbox).where(
            NotificationOutbox.reference_id == pending_id)) == 0


@pytest.mark.parametrize('buyer_changes', [False, True])
async def test_kwork_review_is_queued_only_for_current_assessment(monkeypatch, review_chat, buyer_changes):
    from datetime import UTC, datetime

    from conftest import seed_test_kwork_eligibility

    from app.db.models import Client
    from app.services import kwork_pause

    monkeypatch.setattr(kwork_pause, 'is_kwork_paused', lambda: False)
    job_id, token = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=job_id, platform='kwork', external_id=str(job_id), title='t', description='d',
                        budget_min=1000, budget_max=2000, budget_currency='RUB', status='new',
                        processing_claim_token=token, processing_claim_owner='test',
                        processing_claimed_at=datetime.now(UTC)))
        await session.flush()
        await seed_test_kwork_eligibility(session, job_id, price=700)
        job = await session.get(Job, job_id)
        assessment = dict(job.raw_data['opportunity_assessment'])
        if buyer_changes:
            client = await session.get(Client, job.client_id)
            client.metadata_ = {**client.metadata_, 'hire_percent': 49}
        await session.commit()
    state = {'score': 99, 'is_relevant': True, 'proposal_content': 'Текст ответа',
             'opportunity_assessment': assessment}
    if buyer_changes:
        with pytest.raises(RuntimeError, match='assessment'):
            await JobPipelineService().persist_pipeline_results(job_id, state, claim_token=token)
        async with AsyncSessionLocal() as session:
            assert await session.scalar(select(Proposal.id).where(Proposal.job_id == job_id)) is None
    else:
        proposal_id = await JobPipelineService().persist_pipeline_results(job_id, state, claim_token=token)
        async with AsyncSessionLocal() as session:
            pending = await session.scalar(select(TelegramPending).where(TelegramPending.proposal_id == proposal_id))
            assert pending and (await session.get(ProposalApprovalSnapshot, pending.id)).payload['offer_price'] == 700
