import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Router

from app.telegram.access import edit_preview_binding, edit_preview_matches_callback
from app.telegram.inbox_handlers import register_inbox_handlers


def message(chat=100, number=20):
    return SimpleNamespace(chat=SimpleNamespace(id=chat), message_id=number)


@pytest.mark.parametrize(
    "chat,number,content", [(101, 20, "draft"), (100, 19, "draft"), (100, 20, "changed")]
)
def test_preview_rejects_wrong_chat_old_message_or_changed_draft(chat, number, content):
    data = {"pending_id": "p", **edit_preview_binding(message(), "draft")}
    assert not edit_preview_matches_callback(
        data, SimpleNamespace(message=message(chat, number)), "p", content
    )


def test_preview_accepts_exact_binding_and_rejects_missing_binding():
    callback = SimpleNamespace(message=message())
    data = {"pending_id": "p", **edit_preview_binding(message(), "draft")}
    assert edit_preview_matches_callback(data, callback, "p", "draft")
    assert not edit_preview_matches_callback(data, callback, "other", "draft")
    assert not edit_preview_matches_callback({"pending_id": "p"}, callback, "p", "draft")


def inbox_handler(name):
    router = Router()
    register_inbox_handlers(router)
    return next(h.callback for h in router.callback_query.handlers if h.callback.__name__ == name)


@pytest.mark.asyncio
async def test_old_preview_cannot_send_or_clear_current_edit():
    data = {
        "pending_id": "p",
        "draft_reply": "new",
        **edit_preview_binding(message(number=21), "new"),
    }
    state = SimpleNamespace(get_data=AsyncMock(return_value=data), clear=AsyncMock())
    callback = SimpleNamespace(
        data="inbox_edit_send:p", message=message(number=20), answer=AsyncMock()
    )
    await inbox_handler("handle_inbox_edit_send")(callback, state)
    callback.answer.assert_awaited_once_with("Сессия устарела", show_alert=True)
    state.clear.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", [False, True])
async def test_current_inbox_preview_rebinds_card_before_send(monkeypatch, blocked):
    from app.telegram import inbox_handlers

    pending_id = str(uuid.uuid4())
    pending = SimpleNamespace(
        id=uuid.UUID(pending_id),
        status="pending",
        chat_id=100,
        message_id=10,
        draft_reply="original",
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=pending), commit=AsyncMock())
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(inbox_handlers, "AsyncSessionLocal", lambda: context)
    monkeypatch.setattr(inbox_handlers, "delivery_blocks_mutation", AsyncMock(return_value=blocked))
    monkeypatch.setattr(
        inbox_handlers, "inbox_presentation_is_current", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(inbox_handlers, "record_inbox_approval", AsyncMock())
    send = AsyncMock()
    monkeypatch.setattr(inbox_handlers, "_send_inbox_reply", send)
    data = {
        "pending_id": pending_id,
        "draft_reply": "new",
        "original_reply": "original",
        "original_chat_id": 100,
        "original_message_id": 10,
        **edit_preview_binding(message(), "new"),
    }
    state = SimpleNamespace(get_data=AsyncMock(return_value=data), clear=AsyncMock())
    callback = SimpleNamespace(
        data=f"inbox_edit_send:{pending_id}",
        message=message(),
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=1),
    )
    await inbox_handler("handle_inbox_edit_send")(callback, state)
    if blocked:
        assert (pending.message_id, pending.draft_reply, pending.status) == (
            10,
            "original",
            "pending",
        )
        session.commit.assert_not_awaited()
        state.clear.assert_not_awaited()
        send.assert_not_awaited()
        return
    assert (pending.message_id, pending.draft_reply, pending.status) == (20, "new", "edited")
    session.commit.assert_awaited_once()
    send.assert_awaited_once_with(callback, pending_id, edited=True, reply_text="new")


@pytest.mark.asyncio
async def test_more_invalidates_send_button_before_next_draft():
    data = {"pending_id": "p", "draft_reply": "draft", **edit_preview_binding(message(), "draft")}

    async def update(**values):
        data.update(values)

    state = SimpleNamespace(
        get_data=AsyncMock(return_value=data), update_data=update, set_state=AsyncMock()
    )
    preview = message()
    preview.answer = AsyncMock()
    callback = SimpleNamespace(data="inbox_edit_more:p", message=preview, answer=AsyncMock())
    await inbox_handler("handle_inbox_edit_more")(callback, state)
    assert not edit_preview_matches_callback(data, callback, "p", "draft")
