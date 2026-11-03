"""Mutable approved status alone must never authorize delivery."""

import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def seed():
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal

    jid, pid = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=jid,
                platform="fake",
                external_id=str(jid),
                title="Fake",
                description="Fake",
                status="approved",
            )
        )
        await session.flush()
        session.add(
            Proposal(id=pid, job_id=jid, content="Approved text", version=1, status="approved")
        )
        await session.commit()
    return jid, pid


async def test_status_without_durable_event_never_calls_platform(monkeypatch):
    from unittest.mock import AsyncMock

    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.proposal_sender import ProposalSender

    jid, pid = await seed()
    platform = AsyncMock()
    monkeypatch.setattr(ProposalSender, "_send_to_platform", platform)
    ok, error, _ = await ProposalSender().send(str(jid), str(pid), "Approved text")
    assert not ok and error == "durable_approval_required"
    platform.assert_not_awaited()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(SendAttempt.id).where(SendAttempt.job_id == jid)) is None


@pytest.mark.parametrize("field,value", [("content", "Changed"), ("version", 2)])
async def test_approval_does_not_follow_mutated_draft(field, value):
    from conftest import seed_test_proposal_approval

    from app.db.models import Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import prepare_proposal_send

    jid, pid = await seed()
    async with AsyncSessionLocal() as session:
        await seed_test_proposal_approval(session, jid, pid)
        row = await session.get(Proposal, pid)
        setattr(row, field, value)
        content = row.content
        await session.commit()
    claim, _, _ = await prepare_proposal_send(str(jid), str(pid), content)
    assert claim.action == "blocked" and claim.reason == "durable_approval_required"


async def test_current_event_is_referenced_in_attempt_and_immutable():
    from conftest import seed_test_proposal_approval

    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import prepare_proposal_send

    jid, pid = await seed()
    async with AsyncSessionLocal() as session:
        await seed_test_proposal_approval(session, jid, pid)
    claim, _, _ = await prepare_proposal_send(str(jid), str(pid), "Approved text")
    assert claim.action == "proceed"
    async with AsyncSessionLocal() as session:
        row = await session.get(SendAttempt, claim.attempt_id)
        event_id = uuid.UUID(row.snapshot_metadata["approval_event_id"])
        assert row.snapshot_metadata["approved_payload"]["content"] == row.snapshot_content
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(
                text("UPDATE proposal_approval_events SET actor_user_id=2 WHERE id=:id"),
                {"id": event_id},
            )
        await session.rollback()


async def test_whitespace_change_is_not_the_exact_approved_payload():
    from conftest import seed_test_proposal_approval

    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import prepare_proposal_send

    jid, pid = await seed()
    async with AsyncSessionLocal() as session:
        await seed_test_proposal_approval(session, jid, pid)
    claim, _, _ = await prepare_proposal_send(str(jid), str(pid), " Approved text ")
    assert claim.action == "blocked" and claim.reason == "content_not_approved"


async def test_approval_does_not_follow_changed_budget():
    from conftest import seed_test_proposal_approval

    from app.db.models import Job
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import prepare_proposal_send

    jid, pid = await seed()
    async with AsyncSessionLocal() as session:
        await seed_test_proposal_approval(session, jid, pid)
        row = await session.get(Job, jid)
        row.budget_min = 5000
        await session.commit()
    claim, _, _ = await prepare_proposal_send(str(jid), str(pid), "Approved text")
    assert claim.action == "blocked" and claim.reason == "durable_approval_required"


async def test_edited_preview_approval_preserves_presented_parameters():
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import (
        capture_proposal_card,
        matching_proposal_approval,
        record_proposal_approval,
    )

    jid, pid = await seed()
    async with AsyncSessionLocal() as session:
        job, proposal = await session.get(Job, jid), await session.get(Proposal, pid)
        pending_id = uuid.uuid4()
        await capture_proposal_card(session, pending_id, job, proposal)
        proposal.content, proposal.version = "Edited and reviewed", 2
        event = await record_proposal_approval(
            session,
            pending_id,
            job,
            proposal,
            actor_user_id=5,
            chat_id=100,
            message_id=77,
            approval_kind="edited_preview",
        )
        await session.commit()
        assert (event.actor_user_id, event.message_id) == (5, 77)
        assert (await matching_proposal_approval(session, job, proposal)).id == event.id


async def test_recording_event_without_presented_snapshot_is_rejected():
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import record_proposal_approval

    jid, pid = await seed()
    async with AsyncSessionLocal() as session:
        job, proposal = await session.get(Job, jid), await session.get(Proposal, pid)
        with pytest.raises(ValueError, match="Missing presented"):
            await record_proposal_approval(
                session, uuid.uuid4(), job, proposal, actor_user_id=5, chat_id=100, message_id=77
            )
