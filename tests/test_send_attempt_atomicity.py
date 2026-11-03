"""Concurrent delivery claims on isolated PostgreSQL; no platform calls."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="explicit isolated PostgreSQL required",
)


async def seed():
    from app.db.models import Job, Proposal
    from app.db.session import AsyncSessionLocal

    jid, pid = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(
                id=jid,
                platform="kwork",
                external_id=f"atomic-{jid}",
                title="Fake",
                description="Fake",
                status="approved",
            )
        )
        await session.commit()
        session.add(
            Proposal(id=pid, job_id=jid, content="Approved offer", version=1, status="approved")
        )
        await session.commit()
    from conftest import seed_test_kwork_eligibility, seed_test_proposal_approval

    async with AsyncSessionLocal() as session:
        await seed_test_kwork_eligibility(session, jid)
        await seed_test_proposal_approval(session, jid, pid)
    return jid, pid


def params(jid, pid, text="Approved offer"):
    from app.services.send_attempt_service import proposal_idempotency_key

    return {
        "kind": "proposal",
        "idempotency_key": proposal_idempotency_key(pid, 1, text),
        "snapshot_content": text,
        "snapshot_version": 1,
        "snapshot_metadata": {},
        "job_id": jid,
        "proposal_id": pid,
    }


async def test_parallel_initial_claims_have_one_winner_and_no_integrity_error():
    from app.services.send_attempt_service import claim_send_attempt

    jid, pid = await seed()
    result = await asyncio.gather(
        *[claim_send_attempt(**params(jid, pid)) for _ in range(8)], return_exceptions=True
    )
    assert not any(isinstance(item, Exception) for item in result), result
    assert sum(item.action == "proceed" for item in result) == 1


async def test_parallel_retry_claims_have_one_winner():
    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import claim_send_attempt

    jid, pid = await seed()
    async with AsyncSessionLocal() as session:
        session.add(SendAttempt(**params(jid, pid), status="known_failed"))
        await session.commit()
    results = await asyncio.gather(*[claim_send_attempt(**params(jid, pid)) for _ in range(8)])
    assert sum(item.action == "proceed" for item in results) == 1


@pytest.mark.parametrize("unknown", [False, True])
async def test_other_content_cannot_bypass_active_business_claim(unknown):
    from app.services.send_attempt_service import (
        claim_send_attempt,
        mark_unknown_after_commit_failure,
    )

    jid, pid = await seed()
    first = await claim_send_attempt(**params(jid, pid))
    if unknown:
        await mark_unknown_after_commit_failure(first.attempt_id, first.claim_token, error="fake")
    second = await claim_send_attempt(**params(jid, pid, "Changed text"))
    assert second.action == "blocked"


async def test_late_commit_failure_cannot_overwrite_confirmed_attempt():
    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import (
        claim_send_attempt,
        finalize_send_attempt,
        mark_unknown_after_commit_failure,
    )

    jid, pid = await seed()
    claim = await claim_send_attempt(**params(jid, pid))
    assert await finalize_send_attempt(claim.attempt_id, claim.claim_token, success=True)
    await mark_unknown_after_commit_failure(
        claim.attempt_id, claim.claim_token, error="late failure"
    )
    async with AsyncSessionLocal() as session:
        assert (await session.get(SendAttempt, claim.attempt_id)).status == "confirmed_sent"


async def test_whitespace_only_proposal_change_is_not_marked_sent():
    from app.db.models import Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import claim_send_attempt, finalize_send_attempt

    jid, pid = await seed()
    claim = await claim_send_attempt(**params(jid, pid))
    assert claim.action == "proceed"
    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, pid)
        proposal.content = " Approved offer "
        await session.commit()
    assert await finalize_send_attempt(claim.attempt_id, claim.claim_token, success=True)
    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, pid)
        assert proposal.status == "approved"
        assert proposal.content == " Approved offer "


async def test_unapproved_or_changed_text_is_rejected_before_claim():
    from app.db.models import Proposal, SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import prepare_proposal_send

    jid, pid = await seed()
    changed, _, _ = await prepare_proposal_send(str(jid), str(pid), "Not approved")
    assert changed.action == "blocked"
    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, pid)
        proposal.status = "draft"
        await session.commit()
    draft, _, _ = await prepare_proposal_send(str(jid), str(pid), "Approved offer")
    assert draft.action == "blocked"
    async with AsyncSessionLocal() as session:
        assert (
            not (await session.execute(select(SendAttempt).where(SendAttempt.job_id == jid)))
            .scalars()
            .all()
        )


async def test_expired_claim_blocks_without_network_reconciliation(monkeypatch):
    from unittest.mock import AsyncMock

    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services import send_attempt_reconcile as reconcile
    from app.services.send_attempt_service import claim_send_attempt

    jid, pid = await seed()
    first = await claim_send_attempt(**params(jid, pid))
    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, first.attempt_id)
        attempt.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    network = AsyncMock(side_effect=AssertionError("claim must not call platform"))
    monkeypatch.setattr(reconcile, "reconcile_send_attempt", network)
    assert (await claim_send_attempt(**params(jid, pid))).action == "blocked"
    network.assert_not_called()


@pytest.mark.parametrize("kind", ["proposal", "inbox_reply"])
async def test_two_processes_share_one_business_claim(kind):
    import json
    import sys

    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal

    jid, pid = await seed()
    args = params(jid, pid)
    if kind == "inbox_reply":
        pending_id = uuid.uuid4()
        async with AsyncSessionLocal() as session:
            session.add(
                TelegramInboxPending(
                    id=pending_id,
                    job_id=jid,
                    proposal_id=pid,
                    kwork_user_id=7,
                    kwork_username="buyer",
                    client_message="Fake",
                    draft_reply="Approved offer",
                    status="approved",
                    chat_id=1,
                )
            )
            await session.commit()
        args.update(
            kind="inbox_reply",
            inbox_pending_id=pending_id,
            idempotency_key=f"process:{pending_id}",
            snapshot_version=None,
        )
    payload = {
        key: str(value) if isinstance(value, uuid.UUID) else value for key, value in args.items()
    }
    code = """
import asyncio,json,sys,uuid
from app.db.session import engine
from app.services.send_attempt_service import claim_send_attempt
args=json.loads(sys.argv[1])
for key in ("job_id","proposal_id","inbox_pending_id"):
    if args.get(key): args[key]=uuid.UUID(args[key])
print("READY",flush=True)
sys.stdin.readline()
async def run():
    result=await claim_send_attempt(**args)
    await engine.dispose()
    print("RESULT:"+result.action,flush=True)
asyncio.run(run())
"""
    processes = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            json.dumps(payload),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            assert await asyncio.wait_for(process.stdout.readline(), 30) == b"READY\n"
        for process in processes:
            process.stdin.write(b"go\n")
            await process.stdin.drain()
        actions = []
        for process in processes:
            output, error = await asyncio.wait_for(process.communicate(), 30)
            assert process.returncode == 0, error.decode()
            actions.append(
                next(
                    line[7:] for line in output.decode().splitlines() if line.startswith("RESULT:")
                )
            )
        assert actions.count("proceed") == 1
        assert actions.count("blocked") == 1
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def test_concurrent_proposal_senders_call_platform_once(monkeypatch):
    from app.services.proposal_sender import ProposalSender

    jid, pid = await seed()
    calls = []

    async def platform(self, job, proposal, content, **kwargs):
        calls.append(content)
        await asyncio.sleep(0.05)
        return True, None, {"mock_platform": True}

    monkeypatch.setattr(ProposalSender, "_send_to_platform", platform)
    await asyncio.gather(
        *[ProposalSender().send(str(jid), str(pid), "Approved offer") for _ in range(8)]
    )
    assert calls == ["Approved offer"]


async def test_concurrent_inbox_senders_call_platform_once(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.config import Settings
    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.services import kwork_inbox

    jid, pid = await seed()
    pending_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            TelegramInboxPending(
                id=pending_id,
                job_id=jid,
                proposal_id=pid,
                kwork_user_id=7,
                kwork_username="buyer",
                client_message="Fake",
                draft_reply="Approved answer",
                status="approved",
                chat_id=1,
            )
        )
        await session.commit()
    from conftest import seed_test_inbox_approval

    async with AsyncSessionLocal() as session:
        await seed_test_inbox_approval(session, pending_id)
    calls = []

    async def send(user, content):
        calls.append((user, content))
        await asyncio.sleep(0.05)

    api = SimpleNamespace(
        send_message=AsyncMock(side_effect=send), mark_read=AsyncMock(), close=AsyncMock()
    )
    monkeypatch.setattr(kwork_inbox, "create_kwork_api", AsyncMock(return_value=api))
    cfg = Settings(_env_file=None, PLATFORM_DRY_RUN=False)
    await asyncio.gather(
        *[
            kwork_inbox.send_kwork_inbox_reply(
                user_id=7,
                username="buyer",
                reply_text="Approved answer",
                pending_id=pending_id,
                job_id=jid,
                proposal_id=pid,
                settings=cfg,
            )
            for _ in range(8)
        ]
    )
    assert calls == [(7, "Approved answer")]


async def test_finalization_does_not_overwrite_a_newer_draft():
    from app.db.models import Proposal
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import claim_send_attempt, finalize_send_attempt

    jid, pid = await seed()
    claim = await claim_send_attempt(**params(jid, pid))
    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, pid)
        proposal.version = 2
        proposal.content = "New draft"
        proposal.status = "draft"
        await session.commit()
    assert await finalize_send_attempt(claim.attempt_id, claim.claim_token, success=True)
    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, pid)
        assert (proposal.version, proposal.content, proposal.status) == (2, "New draft", "draft")


async def test_old_token_cannot_finalize_or_mark_unknown_a_retried_claim():
    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import (
        claim_send_attempt,
        finalize_send_attempt,
        mark_unknown_after_commit_failure,
    )

    jid, pid = await seed()
    first = await claim_send_attempt(**params(jid, pid))
    assert await finalize_send_attempt(
        first.attempt_id, first.claim_token, success=False, error="safe validation failure"
    )
    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, first.attempt_id)
        attempt.next_attempt_at = None
        await session.commit()
    second = await claim_send_attempt(**params(jid, pid))
    assert second.action == "proceed" and second.claim_token != first.claim_token
    assert not await finalize_send_attempt(first.attempt_id, first.claim_token, success=True)
    await mark_unknown_after_commit_failure(
        first.attempt_id, first.claim_token, error="old failure"
    )
    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, first.attempt_id)
        assert attempt.status == "sending" and attempt.claim_token == second.claim_token


async def test_inbox_draft_or_changed_payload_requires_explicit_approval():
    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import prepare_inbox_reply_send

    jid, pid = await seed()
    pending_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            TelegramInboxPending(
                id=pending_id,
                job_id=jid,
                proposal_id=pid,
                kwork_user_id=7,
                kwork_username="buyer",
                client_message="Fake",
                draft_reply="Approved answer",
                status="pending",
                chat_id=1,
            )
        )
        await session.commit()
    assert (
        await prepare_inbox_reply_send(
            pending_id=pending_id, reply_text="Approved answer", expected_user_id=7
        )
    ).action == "blocked"
    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramInboxPending, pending_id)
        pending.status = "approved"
        await session.commit()
    assert (
        await prepare_inbox_reply_send(
            pending_id=pending_id, reply_text="Other text", expected_user_id=7
        )
    ).action == "blocked"
    assert (
        await prepare_inbox_reply_send(
            pending_id=pending_id, reply_text="Approved answer", expected_user_id=9
        )
    ).action == "blocked"
