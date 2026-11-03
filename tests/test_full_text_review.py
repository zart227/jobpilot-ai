from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.telegram.full_review import send_full_review, split_review_text


def test_chunks_preserve_whitespace_markup_and_astral_characters():
    content = "  **<tag>& —\n\n\n" + "😀" * 4000 + "\nEND  "
    chunks = split_review_text(content)
    assert "".join(chunks) == content
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 3000 for chunk in chunks)


@pytest.mark.asyncio
async def test_all_text_is_shown_plain_before_only_final_message_gets_buttons():
    text = " <b>&**\n" + "😀" * 4000 + " END  "
    send = AsyncMock(return_value=SimpleNamespace(message_id=99))
    markup = object()
    final = await send_full_review(
        send, content=text, reference="card", heading="Review", markup=markup
    )
    calls = send.await_args_list
    chunks = split_review_text(text)
    assert len(calls) == len(chunks) + 2
    assert "".join(call.args[0].split("\n\n", 1)[1] for call in calls[1:-1]) == text
    assert all(call.kwargs["parse_mode"] is None for call in calls)
    assert all(call.kwargs["reply_markup"] is None for call in calls[:-1])
    assert calls[-1].kwargs["reply_markup"] is markup
    assert final.message_id == 99


@pytest.mark.asyncio
async def test_partial_delivery_failure_exposes_no_approval_controls():
    send = AsyncMock(side_effect=[SimpleNamespace(message_id=1), RuntimeError("timeout")])
    with pytest.raises(RuntimeError):
        await send_full_review(
            send, content="text", reference="card", heading="Review", markup=object()
        )
    assert all(call.kwargs["reply_markup"] is None for call in send.await_args_list)


@pytest.mark.asyncio
async def test_legacy_notification_is_refused_without_sending(monkeypatch):
    from app.services import notification_outbox

    send = AsyncMock()
    row = SimpleNamespace(
        payload={"alert_text": "old", "pending_id": "p"},
        reference_id="p",
        kind="inbox_alert",
        chat_id=1,
    )
    with pytest.raises(ValueError, match="Legacy"):
        await notification_outbox._send_outbox_row(SimpleNamespace(send_message=send), row)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_blank_inbox_draft_only_offers_edit_or_skip(monkeypatch):
    from app.services import notification_outbox

    send = AsyncMock(return_value=SimpleNamespace(message_id=55))
    monkeypatch.setattr(notification_outbox, "_mark_delivery_success", AsyncMock(return_value=True))
    monkeypatch.setattr(notification_outbox, "_claim_is_active", AsyncMock(return_value=True))
    row = SimpleNamespace(
        id="e",
        claim_token="synthetic-token",
        payload={
            "alert_text": "old",
            "pending_id": "p",
            "review_format": "full_text_v1",
            "review_content": "",
            "review_heading": "😀" * 4000,
        },
        reference_id="p",
        kind="inbox_alert",
        chat_id=1,
    )
    await notification_outbox._send_outbox_row(SimpleNamespace(send_message=send), row)
    markup = send.await_args.kwargs["reply_markup"]
    callbacks = [button.callback_data for buttons in markup.inline_keyboard for button in buttons]
    assert "inbox_edit:p" in callbacks
    assert "inbox_approve:p" not in callbacks
    assert all(len(call.args[1].encode("utf-16-le")) // 2 <= 4096 for call in send.await_args_list)


@pytest.mark.asyncio
async def test_long_heading_is_split_without_controls_or_telegram_overflow():
    send = AsyncMock(return_value=SimpleNamespace(message_id=99))
    await send_full_review(
        send, content="Exact body", reference="card", heading="😀" * 4000, markup=object()
    )
    assert all(len(call.args[0].encode("utf-16-le")) // 2 <= 4096 for call in send.await_args_list)
    assert all(call.kwargs["reply_markup"] is None for call in send.await_args_list[:-1])
