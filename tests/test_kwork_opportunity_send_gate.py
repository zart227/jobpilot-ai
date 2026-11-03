"""Policy checks on legacy and current Kwork approvals in an isolated database."""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get('JOBPILOT_INTEGRATION_TESTS') != '1', reason='isolated DB required'
)


async def seed(*, valid):
    from conftest import seed_test_proposal_approval

    from app.db.models import Client, Job, Proposal, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.services.opportunity_policy import kwork_opportunity_fingerprint

    job_id, proposal_id, client_id, pending_id = (uuid.uuid4() for _ in range(4))
    facts = {
        'id': str(job_id), 'client_id': str(client_id), 'platform': 'kwork', 'title': 'Small fix',
        'description': 'Fix a single bug', 'budget_min': 1000.0, 'budget_max': 2000.0,
        'budget_currency': 'RUB', 'client_projects_posted': 5, 'client_hire_percent': 50, 'client_stats_source': 'kwork_buyer_block_v1',
    }
    assessment = {
        'relevant': True, 'assessment_id': 'e48e15f8-6d76-4076-9631-78bf927c5719', 'source_fingerprint': kwork_opportunity_fingerprint(facts),
        'estimated_hours': 2, 'estimated_calendar_days': 1, 'scope_clear': True,
        'complexity': 'simple', 'minimum_price_rub': 700,
    }
    async with AsyncSessionLocal() as session:
        session.add(Client(id=client_id, platform='kwork', external_id=str(client_id),
                           metadata_={'projects_posted': 5, 'hire_percent': 50, 'stats_source': 'kwork_buyer_block_v1'}))
        session.add(Job(id=job_id, platform='kwork', external_id=str(job_id), title=facts['title'],
                        description=facts['description'], budget_min=1000, budget_max=2000,
                        budget_currency='RUB', client_id=client_id, status='approved',
                        raw_data={'opportunity_assessment': assessment} if valid else {}))
        await session.flush()
        session.add(Proposal(id=proposal_id, job_id=job_id, content='Approved', version=1, status='approved'))
        session.add(TelegramPending(id=pending_id, job_id=job_id, proposal_id=proposal_id,
                                    chat_id=100, message_id=44, status='approved'))
        await session.commit()
    async with AsyncSessionLocal() as session:
        await seed_test_proposal_approval(session, job_id, proposal_id)
    return job_id, proposal_id, client_id, pending_id


@pytest.mark.parametrize(('valid', 'expected'), [(False, 'blocked'), (True, 'proceed')])
async def test_send_blocks_legacy_and_allows_current_assessment(valid, expected):
    from app.services.send_attempt_service import prepare_proposal_send

    job_id, proposal_id, _, _ = await seed(valid=valid)
    claim, _, _ = await prepare_proposal_send(str(job_id), str(proposal_id), 'Approved')
    assert claim.action == expected
    if not valid:
        assert claim.reason == 'assessment_required'


async def test_changed_buyer_blocks_send_and_legacy_reissue():
    from app.config import Settings
    from app.db.models import Client
    from app.db.session import AsyncSessionLocal
    from app.services.review_reissue import reissue_review
    from app.services.send_attempt_service import prepare_proposal_send

    job_id, proposal_id, client_id, pending_id = await seed(valid=True)
    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        client.metadata_ = {'projects_posted': 5, 'hire_percent': 49, 'stats_source': 'kwork_buyer_block_v1'}
        await session.commit()
    claim, _, _ = await prepare_proposal_send(str(job_id), str(proposal_id), 'Approved')
    assert claim.action == 'blocked'
    assert claim.reason == 'buyer_not_eligible'
    result = await reissue_review('proposal', pending_id, chat_id=100, user_id=5,
                                  settings=Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID='100',
                                                    TELEGRAM_ADMIN_USER_ID='5'))
    assert result.action == 'blocked'
    assert result.reason == 'buyer_not_eligible'


async def test_new_identical_assessment_never_reuses_old_approval():
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import matching_proposal_approval, proposal_card_is_current
    from app.services.send_attempt_service import prepare_proposal_send

    job_id, proposal_id, _, pending_id = await seed(valid=True)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        proposal = await session.get(Proposal, proposal_id)
        assert await matching_proposal_approval(session, job, proposal)
        assessment = dict(job.raw_data['opportunity_assessment'])
        assessment['assessment_id'] = str(uuid.uuid4())
        job.raw_data = {**job.raw_data, 'opportunity_assessment': assessment}
        await session.commit()
        assert not await matching_proposal_approval(session, job, proposal)
        assert not await proposal_card_is_current(session, pending_id, job, proposal)
    claim, _, _ = await prepare_proposal_send(str(job_id), str(proposal_id), 'Approved')
    assert claim.action == 'blocked' and claim.reason == 'durable_approval_required'


async def test_conflicting_price_in_old_draft_blocks_review_and_send():
    from app.config import Settings
    from app.db.models import Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.review_reissue import reissue_review
    from app.services.send_attempt_service import prepare_proposal_send

    job_id, proposal_id, _, pending_id = await seed(valid=True)
    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, proposal_id)
        proposal.content = 'Выполню задачу за 500 ₽.'
        await session.commit()
    cfg = Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID='100', TELEGRAM_ADMIN_USER_ID='5')
    result = await reissue_review('proposal', pending_id, chat_id=100, user_id=5, settings=cfg)
    assert result.action == 'blocked' and result.reason == 'price_text_mismatch'
    claim, _, _ = await prepare_proposal_send(str(job_id), str(proposal_id), 'Выполню задачу за 500 ₽.')
    assert claim.action == 'blocked'
