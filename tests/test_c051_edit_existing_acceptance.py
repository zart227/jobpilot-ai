"""C051: new_offer vs edit_existing_offer have separate approval, idempotency and send rules."""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="explicit isolated PostgreSQL required",
)


async def _seed_job_proposal(session):
    from app.db.models import Job, Proposal

    jid, pid = uuid.uuid4(), uuid.uuid4()
    session.add(
        Job(
            id=jid,
            platform="kwork",
            external_id=f"c051-{jid}",
            title="C051 job",
            description="Desc",
            status="approved",
            url="https://kwork.ru/projects/1234567/view",
            budget_min=5000,
            budget_max=10000,
            budget_currency="RUB",
        )
    )
    await session.flush()
    content = "Approved offer text for C051 testing here with enough length."
    session.add(
        Proposal(
            id=pid,
            job_id=jid,
            content=content,
            version=1,
            status="approved",
        )
    )
    await session.flush()
    from conftest import seed_test_kwork_eligibility

    await seed_test_kwork_eligibility(session, jid)
    return jid, pid, content


async def test_new_offer_send_blocked_after_live_sent():
    from conftest import seed_test_proposal_approval

    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import prepare_proposal_send, proposal_idempotency_key
    from app.services.submission_policy import SUBMISSION_NEW_OFFER

    async with AsyncSessionLocal() as session:
        jid, pid, content = await _seed_job_proposal(session)
        await seed_test_proposal_approval(session, jid, pid)
        job = await session.get(Job, jid)
        proposal = await session.get(Proposal, pid)
        session.add(
            SendAttempt(
                kind="proposal",
                idempotency_key=proposal_idempotency_key(
                    pid, 1, content, submission_action=SUBMISSION_NEW_OFFER
                ),
                job_id=jid,
                proposal_id=pid,
                snapshot_content=content,
                snapshot_version=1,
                snapshot_metadata={},
                status=ATTEMPT_CONFIRMED_SENT,
                platform_response={"dry_run": False},
            )
        )
        proposal.status = "sent"
        job.status = "sent"
        await session.commit()

    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, pid)
        claim, _, _ = await prepare_proposal_send(str(jid), str(pid), proposal.content)
        assert claim.action == "blocked"
        assert claim.reason == "new_offer_after_sent"


async def test_edit_existing_allows_second_attempt_and_distinct_idempotency():
    from app.db.models import Job, Proposal, SendAttempt
    from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import capture_proposal_card, record_proposal_approval
    from app.services.send_attempt_service import prepare_proposal_send, proposal_idempotency_key
    from app.services.submission_policy import SUBMISSION_EDIT_EXISTING, SUBMISSION_NEW_OFFER

    async with AsyncSessionLocal() as session:
        jid, pid, content = await _seed_job_proposal(session)
        job = await session.get(Job, jid)
        proposal = await session.get(Proposal, pid)
        pending_first = uuid.uuid4()
        await capture_proposal_card(session, pending_first, job, proposal)
        await record_proposal_approval(
            session,
            pending_first,
            job,
            proposal,
            actor_user_id=1,
            chat_id=1,
            message_id=1,
        )
        session.add(
            SendAttempt(
                kind="proposal",
                idempotency_key=proposal_idempotency_key(
                    pid, 1, content, submission_action=SUBMISSION_NEW_OFFER
                ),
                job_id=jid,
                proposal_id=pid,
                snapshot_content=content,
                snapshot_version=1,
                snapshot_metadata={},
                status=ATTEMPT_CONFIRMED_SENT,
            )
        )
        proposal.status = "sent"
        job.status = "sent"
        await session.commit()

    content_v2 = content + " Updated."
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, jid)
        proposal = await session.get(Proposal, pid)
        proposal.content = content_v2
        proposal.version = 2
        proposal.status = "approved"
        pending_edit = uuid.uuid4()
        await capture_proposal_card(session, pending_edit, job, proposal)
        await record_proposal_approval(
            session,
            pending_edit,
            job,
            proposal,
            actor_user_id=1,
            chat_id=1,
            message_id=3,
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        claim, _, _ = await prepare_proposal_send(str(jid), str(pid), content_v2)
        assert claim.action == "proceed"
        assert claim.claim_token is not None

    new_key = proposal_idempotency_key(pid, 2, content_v2, submission_action=SUBMISSION_EDIT_EXISTING)
    old_key = proposal_idempotency_key(pid, 1, content, submission_action=SUBMISSION_NEW_OFFER)
    assert new_key != old_key
    assert new_key.startswith("proposal_edit:")


async def test_edited_preview_before_first_send_stays_new_offer():
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import capture_proposal_card, record_proposal_approval
    from app.services.send_attempt_service import prepare_proposal_send
    from app.services.submission_policy import SUBMISSION_NEW_OFFER

    async with AsyncSessionLocal() as session:
        jid, pid, content = await _seed_job_proposal(session)
        job = await session.get(Job, jid)
        proposal = await session.get(Proposal, pid)
        pending_id = uuid.uuid4()
        await capture_proposal_card(session, pending_id, job, proposal)
        edited = content + " v2"
        proposal.content = edited
        proposal.version = 2
        await record_proposal_approval(
            session,
            pending_id,
            job,
            proposal,
            actor_user_id=1,
            chat_id=1,
            message_id=1,
            approval_kind="edited_preview",
        )
        proposal.status = "edited"
        await session.commit()

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        from app.db.models import ProposalApprovalEvent

        event = await session.scalar(select(ProposalApprovalEvent).limit(1))
        assert event.payload["submission_action"] == SUBMISSION_NEW_OFFER
        claim, _, _ = await prepare_proposal_send(str(jid), str(pid), edited)
        assert claim.action == "proceed"
