"""Maintenance regeneration must invalidate old approval cards atomically."""

import asyncio
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def seed():
    from app.db.models import Job, TelegramInboxPending
    from app.db.session import AsyncSessionLocal

    jid, pending_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(
            Job(id=jid, platform="fake", external_id=str(jid), title="Fake", description="Fake")
        )
        await session.commit()
        session.add(
            TelegramInboxPending(
                id=pending_id,
                job_id=jid,
                kwork_user_id=5,
                kwork_username="buyer",
                client_message="Question",
                draft_reply="Original",
                chat_id=100,
                message_id=55,
                status="pending",
            )
        )
        await session.commit()
    return pending_id


@pytest.fixture
def fake_refresh(monkeypatch):
    from app.config import Settings
    from app.services import kwork_inbox as inbox

    monkeypatch.setattr(inbox, "_fetch_kwork_thread", AsyncMock(return_value=[{"id": "1"}]))
    monkeypatch.setattr(
        inbox, "_generate_inbox_reply", AsyncMock(return_value=("answer", "Replacement"))
    )
    return inbox, Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID="100", TELEGRAM_BOT_TOKEN="fake")


async def refresh(inbox, settings, pending_id):
    return await inbox._refresh_pending_inbox_draft(
        pending_id, settings, my_username="me", my_user_id=1
    )


async def test_refresh_creates_new_pending_and_outbox_preserving_original(fake_refresh):
    from app.db.models import NotificationOutbox, TelegramInboxPending
    from app.db.session import AsyncSessionLocal

    inbox, settings = fake_refresh
    pending_id = await seed()
    result = await refresh(inbox, settings, pending_id)
    async with AsyncSessionLocal() as session:
        old = await session.get(TelegramInboxPending, pending_id)
        new = await session.get(TelegramInboxPending, uuid.UUID(result["pending_id"]))
        assert (old.status, old.draft_reply, old.message_id) == ("superseded", "Original", 55)
        assert (new.status, new.draft_reply, new.message_id) == ("pending", "Replacement", None)
        event = await session.scalar(
            select(NotificationOutbox).where(NotificationOutbox.reference_id == new.id)
        )
        assert event.payload["pending_id"] == str(new.id)


@pytest.mark.parametrize(
    "race", ["approved", "edited", "sent", "text", "sending", "unknown_result"]
)
async def test_refresh_discards_result_if_approval_edit_or_send_started(
    fake_refresh, monkeypatch, race
):
    from app.db.models import SendAttempt, TelegramInboxPending
    from app.db.session import AsyncSessionLocal

    inbox, settings = fake_refresh
    pending_id = await seed()

    async def generate(*args, **kwargs):
        async with AsyncSessionLocal() as session:
            row = await session.get(TelegramInboxPending, pending_id)
            if race in ("sending", "unknown_result"):
                session.add(
                    SendAttempt(
                        kind="inbox_reply",
                        inbox_pending_id=pending_id,
                        idempotency_key=str(uuid.uuid4()),
                        snapshot_content="Original",
                        status=race,
                    )
                )
            elif race == "text":
                row.draft_reply = "Human edit"
            else:
                row.status = race
            await session.commit()
        return "answer", "Stale result"

    monkeypatch.setattr(inbox, "_generate_inbox_reply", generate)
    assert await refresh(inbox, settings, pending_id) is None
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pending_id)
        assert row.draft_reply == ("Human edit" if race == "text" else "Original")


async def test_parallel_refresh_has_one_replacement(fake_refresh, monkeypatch):
    inbox, settings = fake_refresh
    pending_id = await seed()
    gate = asyncio.Event()
    arrived = 0

    async def generate(*args, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), 5)
        return "answer", "Replacement"

    monkeypatch.setattr(inbox, "_generate_inbox_reply", generate)
    results = await asyncio.gather(
        refresh(inbox, settings, pending_id), refresh(inbox, settings, pending_id)
    )
    assert sum(item is not None for item in results) == 1


async def test_enqueue_failure_rolls_back_supersede(fake_refresh, monkeypatch):
    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal

    inbox, settings = fake_refresh
    pending_id = await seed()
    monkeypatch.setattr(
        inbox, "enqueue_notification", AsyncMock(side_effect=RuntimeError("fake failure"))
    )
    with pytest.raises(RuntimeError):
        await refresh(inbox, settings, pending_id)
    async with AsyncSessionLocal() as session:
        assert (await session.get(TelegramInboxPending, pending_id)).status == "pending"


async def test_wrapper_queues_card_and_does_not_count_other_outbox_deliveries(
    fake_refresh, monkeypatch
):
    inbox, settings = fake_refresh
    pending_id = await seed()
    monkeypatch.setattr(inbox, "_get_kwork_actor", AsyncMock(return_value=("me", 1)))
    monkeypatch.setattr(
        inbox, "deliver_notification_outbox", AsyncMock(return_value={"delivered": 99})
    )
    result = await inbox.resend_inbox_pending_notifications(settings, limit=1)
    assert result["updated"] == result["queued"] == 1
    assert result["sent"] == 0
    assert result["items"][0]["superseded_pending_id"] == str(pending_id)


async def test_pending_only_false_cannot_reopen_approved_row(fake_refresh, monkeypatch):
    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal

    inbox, settings = fake_refresh
    pending_id = await seed()
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pending_id)
        row.status = "approved"
        await session.commit()
    monkeypatch.setattr(inbox, "_get_kwork_actor", AsyncMock(return_value=("me", 1)))
    refresh_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(inbox, "_refresh_pending_inbox_draft", refresh_mock)
    result = await inbox.regenerate_pending_inbox_drafts(settings, pending_only=False)
    assert result["updated"] == 0
    assert all(call.args[0] != pending_id for call in refresh_mock.await_args_list)
    inbox._generate_inbox_reply.assert_not_awaited()


async def test_unbound_pending_does_not_regenerate_via_username(fake_refresh, monkeypatch):
    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal

    inbox, settings = fake_refresh
    pending_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(TelegramInboxPending(
            id=pending_id, kwork_user_id=5, kwork_username="buyer", client_message="Question",
            draft_reply="", chat_id=100, status="pending",
        ))
        await session.commit()
    fallback = AsyncMock(return_value=type("Match", (), {
        "needs_context": False, "job": object(), "proposal": None,
    })())
    monkeypatch.setattr(inbox, "find_kwork_job_for_message", fallback)
    assert await refresh(inbox, settings, pending_id) is None
    fallback.assert_not_awaited()
    inbox._generate_inbox_reply.assert_not_awaited()
