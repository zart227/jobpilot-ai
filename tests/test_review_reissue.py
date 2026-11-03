"""Explicit review recovery is atomic, idempotent and never a send authorization."""

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


@pytest.fixture
def settings():
    from app.config import Settings

    return Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID="100", TELEGRAM_ADMIN_USER_ID="5")


async def seed(kind, *, status="approved", message_id=44):
    from app.db.models import InboundMessage, Job, Proposal, TelegramInboxPending, TelegramPending
    from app.db.session import AsyncSessionLocal

    jid, pid, source_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
            Proposal(id=pid, job_id=jid, content="Legacy exact offer", version=3, status="approved")
        )
        await session.flush()
        if kind == "proposal":
            session.add(
                TelegramPending(
                    id=source_id,
                    job_id=jid,
                    proposal_id=pid,
                    chat_id=100,
                    message_id=message_id,
                    status=status,
                )
            )
        else:
            session.add(
                TelegramInboxPending(
                    id=source_id,
                    job_id=jid,
                    proposal_id=pid,
                    kwork_user_id=7,
                    kwork_username="buyer",
                    client_message="Question",
                    draft_reply="Legacy exact answer",
                    chat_id=100,
                    message_id=message_id,
                    status=status,
                )
            )
            await session.flush()
            session.add(
                InboundMessage(
                    platform="kwork",
                    message_key=str(uuid.uuid4()),
                    thread_key="7",
                    body="Question",
                    inbox_pending_id=source_id,
                    status="processed",
                    raw_metadata={},
                )
            )
        await session.commit()
    return jid, pid, source_id


async def reissue(kind, source_id, settings, **kwargs):
    from app.services.review_reissue import reissue_review

    return await reissue_review(
        kind, source_id, chat_id=100, user_id=5, settings=settings, **kwargs
    )


@pytest.mark.parametrize("kind", ["proposal", "inbox"])
@pytest.mark.parametrize("status", ["pending", "approved", "edited"])
async def test_legacy_review_reissued_without_granting_approval(kind, status, settings):
    from app.db.models import (
        InboundMessage,
        InboxApprovalRecord,
        NotificationOutbox,
        Proposal,
        ProposalApprovalEvent,
        TelegramInboxPending,
        TelegramPending,
    )
    from app.db.session import AsyncSessionLocal
    from app.services.send_attempt_service import prepare_inbox_reply_send, prepare_proposal_send

    jid, pid, source_id = await seed(kind, status=status)
    result = await reissue(kind, source_id, settings, source_message_id=44)
    assert result.action == "queued" and result.pending_id != source_id
    model = TelegramPending if kind == "proposal" else TelegramInboxPending
    async with AsyncSessionLocal() as session:
        old = await session.get(model, source_id)
        new = await session.get(model, result.pending_id)
        assert (old.status, old.message_id) == ("superseded", 44)
        assert (new.status, new.message_id) == ("pending", None)
        event = await session.get(NotificationOutbox, result.outbox_id)
        assert event.payload["review_format"] == "full_text_v1"
        assert event.payload["reissued_from"] == str(source_id)
        assert event.payload["requested_by"] == 5
        if kind == "proposal":
            proposal = await session.get(Proposal, pid)
            assert (proposal.content, proposal.version, proposal.status) == (
                "Legacy exact offer",
                3,
                "pending_approval",
            )
            assert (
                await session.scalar(
                    select(ProposalApprovalEvent.id).where(
                        ProposalApprovalEvent.pending_id == new.id
                    )
                )
                is None
            )
        else:
            assert new.draft_reply == old.draft_reply == "Legacy exact answer"
            assert (
                await session.scalars(
                    select(InboxApprovalRecord.record_kind).where(
                        InboxApprovalRecord.pending_id == new.id
                    )
                )
            ).all() == ["presented"]
            assert (
                await session.scalar(
                    select(InboundMessage.id).where(InboundMessage.inbox_pending_id == source_id)
                )
                is None
            )
    if kind == "proposal":
        claim, _, _ = await prepare_proposal_send(str(jid), str(pid), "Legacy exact offer")
    else:
        claim = await prepare_inbox_reply_send(
            pending_id=result.pending_id, reply_text="Legacy exact answer", expected_user_id=7
        )
    assert claim.action == "blocked"


@pytest.mark.parametrize("kind", ["proposal", "inbox"])
async def test_parallel_requests_create_one_replacement(kind, settings):
    from app.db.models import NotificationOutbox
    from app.db.session import AsyncSessionLocal

    _, _, source = await seed(kind)
    results = await asyncio.gather(*[reissue(kind, source, settings) for _ in range(8)])
    assert sum(r.action == "queued" for r in results) == 1
    assert sum(r.action == "already_queued" for r in results) == 7
    assert len({r.pending_id for r in results}) == 1
    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(NotificationOutbox)
                .where(NotificationOutbox.idempotency_key == f"review_reissue:{kind}:{source}")
            )
            == 1
        )


@pytest.mark.parametrize("kind", ["proposal", "inbox"])
@pytest.mark.parametrize(
    "state", ["sending", "unknown_result", "needs_attention", "confirmed_sent"]
)
async def test_delivery_state_cannot_be_bypassed_by_reissue(kind, state, settings):
    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal

    jid, pid, source = await seed(kind)
    async with AsyncSessionLocal() as session:
        session.add(
            SendAttempt(
                kind="proposal" if kind == "proposal" else "inbox_reply",
                idempotency_key=str(uuid.uuid4()),
                job_id=jid,
                proposal_id=pid,
                inbox_pending_id=source if kind == "inbox" else None,
                status=state,
                snapshot_content="Legacy text",
            )
        )
        await session.commit()
    result = await reissue(kind, source, settings)
    assert result.action == "blocked" and result.reason == "delivery_requires_resolution"


@pytest.mark.parametrize("kind", ["proposal", "inbox"])
async def test_outbox_failure_rolls_back_new_pending_and_supersede(kind, settings, monkeypatch):
    from app.db.models import TelegramInboxPending, TelegramPending
    from app.db.session import AsyncSessionLocal
    from app.services import review_reissue

    jid, _, source = await seed(kind)
    monkeypatch.setattr(
        review_reissue, "enqueue_notification", AsyncMock(side_effect=RuntimeError("fake failure"))
    )
    with pytest.raises(RuntimeError):
        await reissue(kind, source, settings)
    model = TelegramPending if kind == "proposal" else TelegramInboxPending
    async with AsyncSessionLocal() as session:
        assert (await session.get(model, source)).status == "approved"
        assert (
            await session.scalar(select(func.count()).select_from(model).where(model.job_id == jid))
            == 1
        )


@pytest.mark.parametrize("kind", ["proposal", "inbox"])
@pytest.mark.parametrize("status", ["sent", "skipped", "superseded"])
async def test_processed_source_is_not_reopened(kind, status, settings):
    _, _, source = await seed(kind, status=status)
    result = await reissue(kind, source, settings)
    assert result.action == "blocked" and result.reason == "protected_status"


async def test_admin_identity_checked_before_database_access(settings, monkeypatch):
    from app.services import review_reissue

    def forbidden():
        raise AssertionError("No database access for unauthorized users")

    monkeypatch.setattr(review_reissue, "AsyncSessionLocal", forbidden)
    result = await review_reissue.reissue_review(
        "proposal", uuid.uuid4(), chat_id=100, user_id=99, settings=settings
    )
    assert result.reason == "unauthorized"


async def test_reply_id_and_cross_chat_checked(settings):
    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal

    _, _, source = await seed("inbox")
    assert (await reissue("inbox", source, settings, source_message_id=99)).reason == "stale_card"
    async with AsyncSessionLocal() as session:
        (await session.get(TelegramInboxPending, source)).chat_id = 101
        await session.commit()
    assert (await reissue("inbox", source, settings)).reason == "chat_mismatch"


async def test_explicit_id_can_recover_a_card_with_no_message_id(settings):
    _, _, source = await seed("proposal", message_id=None)
    assert (await reissue("proposal", source, settings)).action == "queued"


async def test_legacy_sent_history_blocks_inbox_reissue(settings):
    from app.db.models import Interaction
    from app.db.session import AsyncSessionLocal

    _, _, source = await seed("inbox")
    async with AsyncSessionLocal() as session:
        session.add(
            Interaction(
                direction="outbound",
                channel="kwork",
                message="Legacy sent",
                metadata_={"pending_id": str(source)},
            )
        )
        await session.commit()
    assert (await reissue("inbox", source, settings)).reason == "delivery_requires_resolution"


@pytest.mark.parametrize("status", ["filtered", "permanent_error", "sent", "sending"])
async def test_protected_job_not_revived(status, settings):
    from app.db.models import Job
    from app.db.session import AsyncSessionLocal

    jid, _, source = await seed("proposal")
    async with AsyncSessionLocal() as session:
        (await session.get(Job, jid)).status = status
        await session.commit()
    assert (await reissue("proposal", source, settings)).action == "blocked"


async def test_reply_command_uses_real_service_and_never_dispatches_marketplace(
    settings, monkeypatch
):
    from functools import partial

    from aiogram import Router

    from app.services.review_reissue import reissue_review, review_reference_for_message
    from app.telegram import review_handlers

    reply_id = uuid.uuid4().int % 1_000_000_000 + 1000
    await seed("proposal", message_id=reply_id)
    monkeypatch.setattr(review_handlers, "is_admin_chat_message", lambda _: True)
    monkeypatch.setattr(
        review_handlers, "reissue_review", partial(reissue_review, settings=settings)
    )
    monkeypatch.setattr(
        review_handlers,
        "review_reference_for_message",
        partial(review_reference_for_message, settings=settings),
    )
    router = Router()
    review_handlers.register_review_handlers(router)
    message = SimpleNamespace(
        text="/rereview",
        chat=SimpleNamespace(id=100),
        from_user=SimpleNamespace(id=5),
        reply_to_message=SimpleNamespace(message_id=reply_id),
        answer=AsyncMock(),
    )
    await router.message.handlers[0].callback(message)
    assert "поставлена в очередь" in message.answer.await_args.args[0]
    assert "Нужно новое одобрение" in message.answer.await_args.args[0]


@pytest.mark.parametrize("kind", ["proposal", "inbox"])
async def test_reissue_racing_current_approved_send_has_one_winner(kind, settings):
    from app.db.models import Job, Proposal, TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.services.approval_snapshot import capture_proposal_card, record_proposal_approval
    from app.services.inbox_approval import capture_inbox_presentation, record_inbox_approval
    from app.services.send_attempt_service import prepare_inbox_reply_send, prepare_proposal_send

    jid, pid, source = await seed(kind)
    async with AsyncSessionLocal() as session:
        if kind == "proposal":
            job, proposal = await session.get(Job, jid), await session.get(Proposal, pid)
            await capture_proposal_card(session, source, job, proposal)
            await record_proposal_approval(
                session, source, job, proposal, actor_user_id=5, chat_id=100, message_id=44
            )
        else:
            pending = await session.get(TelegramInboxPending, source)
            await capture_inbox_presentation(session, pending)
            await record_inbox_approval(
                session, pending, actor_user_id=5, chat_id=100, message_id=44
            )
        await session.commit()

    async def claim():
        if kind == "proposal":
            result, _, _ = await prepare_proposal_send(str(jid), str(pid), "Legacy exact offer")
            return result
        return await prepare_inbox_reply_send(
            pending_id=source,
            reply_text="Legacy exact answer",
            expected_user_id=7,
            expected_username="buyer",
        )

    review, send = await asyncio.gather(reissue(kind, source, settings), claim())
    assert (review.action == "queued") != (send.action == "proceed")
    if send.action == "proceed":
        assert review.reason == "delivery_requires_resolution"
    else:
        assert send.action == "blocked"


async def test_ambiguous_reply_is_not_guessed(settings):
    from app.services.review_reissue import review_reference_for_message

    message_id = uuid.uuid4().int % 1_000_000_000 + 1000
    await seed("proposal", message_id=message_id)
    await seed("inbox", message_id=message_id)
    assert await review_reference_for_message(100, message_id, user_id=5, settings=settings) is None
