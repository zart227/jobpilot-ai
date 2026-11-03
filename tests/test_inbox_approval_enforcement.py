"""Inbox authorization binds exact payload, actor and current Telegram card."""

import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required"
)


async def seed(*, approve=False):
    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.services.inbox_approval import capture_inbox_presentation, record_inbox_approval

    pid = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        row = TelegramInboxPending(
            id=pid,
            kwork_user_id=7,
            kwork_username="buyer",
            client_message="Question",
            draft_reply="Exact answer",
            status="approved",
            chat_id=100,
            message_id=55,
        )
        session.add(row)
        await session.flush()
        await capture_inbox_presentation(session, row)
        if approve:
            await record_inbox_approval(session, row, actor_user_id=5, chat_id=100, message_id=55)
        await session.commit()
    return pid


async def prepare(pid, content="Exact answer", user=7, username="buyer"):
    from app.services.send_attempt_service import prepare_inbox_reply_send

    return await prepare_inbox_reply_send(
        pending_id=pid, reply_text=content, expected_user_id=user, expected_username=username
    )


async def test_approved_status_without_event_cannot_call_api(monkeypatch):
    from app.config import Settings
    from app.services import kwork_inbox

    pid = await seed()
    create = AsyncMock()
    monkeypatch.setattr(kwork_inbox, "create_kwork_api", create)
    ok, error = await kwork_inbox.send_kwork_inbox_reply(
        pending_id=pid,
        user_id=7,
        username="buyer",
        reply_text="Exact answer",
        settings=Settings(_env_file=None, PLATFORM_DRY_RUN=False),
    )
    assert not ok and error == "durable_approval_required"
    create.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("draft_reply", "Changed"),
        ("kwork_user_id", 8),
        ("kwork_username", "other"),
        ("client_message", "Different question"),
        ("message_id", 56),
    ],
)
async def test_approval_does_not_follow_mutated_payload_or_card(field, value):
    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal

    pid = await seed(approve=True)
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pid)
        setattr(row, field, value)
        args = row.draft_reply, row.kwork_user_id, row.kwork_username
        await session.commit()
    claim = await prepare(pid, *args)
    assert claim.action == "blocked" and claim.reason == "durable_approval_required"


async def test_exact_whitespace_is_required():
    pid = await seed(approve=True)
    claim = await prepare(pid, " Exact answer ")
    assert claim.reason == "content_not_approved"


async def test_current_event_is_linked_to_attempt_and_immutable():
    from app.db.models import SendAttempt
    from app.db.session import AsyncSessionLocal

    pid = await seed(approve=True)
    claim = await prepare(pid)
    assert claim.action == "proceed" and claim.approved_payload["content"] == "Exact answer"
    async with AsyncSessionLocal() as session:
        attempt = await session.get(SendAttempt, claim.attempt_id)
        event_id = uuid.UUID(attempt.snapshot_metadata["approval_event_id"])
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(
                text("UPDATE inbox_approval_records SET actor_user_id=9 WHERE id=:id"),
                {"id": event_id},
            )
        await session.rollback()


async def test_edited_preview_authorizes_new_text_but_not_new_recipient():
    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.services.inbox_approval import record_inbox_approval

    pid = await seed()
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pid)
        row.draft_reply, row.message_id, row.status = "Reviewed edit", 77, "edited"
        await record_inbox_approval(
            session, row, actor_user_id=5, chat_id=100, message_id=77, edited=True
        )
        await session.commit()
    assert (await prepare(pid, "Reviewed edit")).action == "proceed"
    other = await seed()
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, other)
        row.kwork_user_id = 99
        with pytest.raises(ValueError, match="Stale"):
            await record_inbox_approval(
                session, row, actor_user_id=5, chat_id=100, message_id=55, edited=True
            )


async def test_normal_callback_records_actor_and_card_before_dispatch(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.services.inbox_approval import matching_inbox_approval
    from app.telegram import inbox_handlers

    pid = await seed()
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pid)
        row.status = "pending"
        await session.commit()
    dispatch = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(inbox_handlers, "send_kwork_inbox_reply", dispatch)
    message = SimpleNamespace(
        chat=SimpleNamespace(id=100),
        message_id=55,
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock(),
    )
    callback = SimpleNamespace(message=message, from_user=SimpleNamespace(id=5), answer=AsyncMock())
    await inbox_handlers._send_inbox_reply(callback, str(pid), edited=False)
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pid)
        event = await matching_inbox_approval(session, row)
        assert (row.status, event.actor_user_id, event.message_id) == ("approved", 5, 55)
    dispatch.assert_awaited_once()


async def test_edit_callback_commits_exact_preview_event(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram import Router

    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.services.inbox_approval import matching_inbox_approval
    from app.telegram import inbox_handlers
    from app.telegram.access import edit_preview_binding

    pid = await seed()
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pid)
        row.status = "pending"
        await session.commit()
    router = Router()
    inbox_handlers.register_inbox_handlers(router)
    handler = next(
        h.callback
        for h in router.callback_query.handlers
        if h.callback.__name__ == "handle_inbox_edit_send"
    )
    preview = SimpleNamespace(chat=SimpleNamespace(id=100), message_id=77)
    data = {
        "pending_id": str(pid),
        "draft_reply": "Reviewed new text",
        "original_reply": "Exact answer",
        "original_chat_id": 100,
        "original_message_id": 55,
        **edit_preview_binding(preview, "Reviewed new text"),
    }
    state = SimpleNamespace(get_data=AsyncMock(return_value=data), clear=AsyncMock())
    callback = SimpleNamespace(
        data=f"inbox_edit_send:{pid}",
        message=preview,
        from_user=SimpleNamespace(id=5),
        answer=AsyncMock(),
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(inbox_handlers, "_send_inbox_reply", dispatch)
    await handler(callback, state)
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pid)
        event = await matching_inbox_approval(session, row)
        assert (row.draft_reply, event.record_kind, event.message_id) == (
            "Reviewed new text",
            "edited_preview",
            77,
        )
    dispatch.assert_awaited_once()


async def test_stale_skip_does_not_hide_changed_draft():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram import Router

    from app.db.models import TelegramInboxPending
    from app.db.session import AsyncSessionLocal
    from app.telegram.inbox_handlers import register_inbox_handlers

    pid = await seed()
    async with AsyncSessionLocal() as session:
        row = await session.get(TelegramInboxPending, pid)
        row.status, row.draft_reply = "pending", "Changed since presentation"
        await session.commit()
    router = Router()
    register_inbox_handlers(router)
    handler = next(
        h.callback
        for h in router.callback_query.handlers
        if h.callback.__name__ == "handle_inbox_skip"
    )
    callback = SimpleNamespace(
        data=f"inbox_skip:{pid}",
        message=SimpleNamespace(chat=SimpleNamespace(id=100), message_id=55),
        answer=AsyncMock(),
    )
    await handler(callback)
    async with AsyncSessionLocal() as session:
        assert (await session.get(TelegramInboxPending, pid)).status == "pending"
    callback.answer.assert_awaited_once_with("Карточка устарела — запросите новую", show_alert=True)
