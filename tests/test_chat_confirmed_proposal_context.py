"""Chat context must use a confirmed sent snapshot, not a mutable draft."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.db.models import Job, Proposal, SendAttempt
from app.db.session import AsyncSessionLocal
from app.services import conversation_context_service as context

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated database required"
)


@pytest.fixture
def no_history(monkeypatch):
    monkeypatch.setattr(context, "resolve_conversation_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(context, "resolve_conversation_context", AsyncMock(return_value=""))


async def seed(status=None, *, dry_run=False, kind="proposal"):
    jid, pid = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Job(id=jid, platform="kwork", external_id=str(jid), title="Test", description="d"))
        await session.flush()
        session.add(Proposal(id=pid, job_id=jid, content="Changed draft", version=2, status="sent"))
        await session.flush()
        if status:
            session.add(SendAttempt(
                id=uuid.uuid4(), kind=kind, idempotency_key=f"context:{uuid.uuid4()}",
                job_id=jid, proposal_id=pid, snapshot_content="  Sent snapshot\n  ",
                snapshot_version=1, status=status, platform_response={"dry_run": dry_run},
            ))
        await session.commit()
    return jid, pid


async def test_chat_uses_exact_sent_snapshot_not_mutable_proposal(no_history):
    jid, pid = await seed("confirmed_sent")
    async with AsyncSessionLocal() as session:
        state = await context.build_chat_agent_state(session, jid)
    assert state["proposal_content"] == "  Sent snapshot\n  "
    assert state["proposal_id"] == str(pid)


@pytest.mark.parametrize("status,dry_run,kind", [
    (None, False, "proposal"), ("unknown_result", False, "proposal"),
    ("confirmed_sent", True, "proposal"), ("confirmed_sent", False, "inbox_reply"),
])
async def test_unconfirmed_or_simulated_send_never_becomes_chat_context(no_history, status, dry_run, kind):
    jid, _ = await seed(status, dry_run=dry_run, kind=kind)
    async with AsyncSessionLocal() as session:
        state = await context.build_chat_agent_state(session, jid)
    assert state["proposal_content"] == ""
    assert state["proposal_id"] is None


async def test_proposal_from_different_job_is_rejected(no_history):
    jid, _ = await seed("confirmed_sent")
    other_jid, other_pid = await seed("confirmed_sent")
    async with AsyncSessionLocal() as session:
        other = await session.get(Proposal, other_pid)
        state = await context.build_chat_agent_state(session, jid, proposal=other)
    assert other_jid != jid
    assert state["proposal_content"] == "" and state["proposal_id"] is None


async def test_latest_confirmed_non_simulated_snapshot_wins(no_history):
    jid, pid = await seed("confirmed_sent")
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        session.add_all([
            SendAttempt(
                kind="proposal", idempotency_key=f"context:{uuid.uuid4()}", job_id=jid,
                proposal_id=pid, snapshot_content="Latest confirmed", status="confirmed_sent",
                platform_response={"dry_run": False}, finished_at=now,
            ),
            SendAttempt(
                kind="proposal", idempotency_key=f"context:{uuid.uuid4()}", job_id=jid,
                proposal_id=pid, snapshot_content="Later simulation", status="confirmed_sent",
                platform_response={"dry_run": True}, finished_at=now + timedelta(seconds=1),
            ),
        ])
        await session.commit()
        state = await context.build_chat_agent_state(session, jid)
    assert state["proposal_content"] == "Latest confirmed"
    assert state["proposal_id"] == str(pid)
