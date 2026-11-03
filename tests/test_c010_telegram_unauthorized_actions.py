"""C010: unauthorized Telegram identities cannot reach admin actions or /dev."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.config import Settings
from app.telegram.access import is_authorized_telegram


def _admin_settings(**overrides) -> Settings:
    base = dict(
        TELEGRAM_ADMIN_CHAT_ID="100",
        TELEGRAM_ADMIN_USER_ID="100",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.mark.parametrize(
    "callback_data",
    [
        "approve:00000000-0000-0000-0000-000000000001",
        "skip:00000000-0000-0000-0000-000000000001",
        "edit:00000000-0000-0000-0000-000000000001",
        "inbox_approve:00000000-0000-0000-0000-000000000001",
        "inbox_skip:00000000-0000-0000-0000-000000000001",
        "inbox_edit:00000000-0000-0000-0000-000000000001",
        "inbox_edit_send:00000000-0000-0000-0000-000000000001",
    ],
)
def test_c010_callback_admin_actions_require_matching_chat_and_user(callback_data):
    from app.telegram.access import chat_user_from_update

    cfg = _admin_settings()
    update = Update.model_construct(
        update_id=1,
        callback_query=CallbackQuery.model_construct(
            id="cb1",
            from_user=User(id=7, is_bot=False, first_name="Stranger"),
            chat_instance="1",
            data=callback_data,
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=100, type="private"),
            ),
        ),
    )
    chat_id, user_id = chat_user_from_update(update)
    assert chat_id == 100 and user_id == 7
    assert is_authorized_telegram(chat_id, user_id, cfg) is False
    assert is_authorized_telegram(chat_id, 100, cfg) is True


@pytest.mark.asyncio
async def test_c010_middleware_blocks_unauthorized_callback_update(monkeypatch):
    from app.telegram import admin_middleware

    cfg = _admin_settings()
    monkeypatch.setattr(
        admin_middleware,
        "is_authorized_telegram",
        lambda chat, user: is_authorized_telegram(chat, user, cfg),
    )
    answer_mock = AsyncMock()

    async def fake_answer(_self, *args, **kwargs):
        return await answer_mock(*args, **kwargs)

    monkeypatch.setattr(CallbackQuery, "answer", fake_answer, raising=False)
    update = Update.model_construct(
        update_id=1,
        callback_query=CallbackQuery.model_construct(
            id="cb1",
            from_user=User(id=7, is_bot=False, first_name="Stranger"),
            chat_instance="1",
            data="approve:00000000-0000-0000-0000-000000000001",
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=100, type="private"),
            ),
        ),
    )
    handler = AsyncMock()
    await admin_middleware.TelegramAdminMiddleware()(handler, update, {})
    handler.assert_not_called()
    answer_mock.assert_awaited_once()
    assert "доступ" in answer_mock.await_args.args[0].lower()


@pytest.mark.parametrize(
    "text",
    ["/dev", "/dev status", "/rereview proposal 00000000-0000-0000-0000-000000000001"],
)
@pytest.mark.asyncio
async def test_c010_middleware_blocks_unauthorized_commands(text, monkeypatch):
    from app.telegram import admin_middleware

    cfg = _admin_settings()
    monkeypatch.setattr(
        admin_middleware,
        "is_authorized_telegram",
        lambda chat, user: is_authorized_telegram(chat, user, cfg),
    )
    monkeypatch.setattr(Message, "answer", AsyncMock())
    update = Update(
        update_id=2,
        message=Message(
            message_id=2,
            date=datetime.now(UTC),
            chat=Chat(id=100, type="private"),
            from_user=User(id=7, is_bot=False, first_name="Stranger"),
            text=text,
        ),
    )
    handler = AsyncMock()
    await admin_middleware.TelegramAdminMiddleware()(handler, update, {})
    handler.assert_not_called()
    Message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_c010_group_chat_requires_configured_admin_user():
    cfg = Settings(_env_file=None, TELEGRAM_ADMIN_CHAT_ID="-10042", TELEGRAM_ADMIN_USER_ID="42")
    assert is_authorized_telegram(-10042, 42, cfg) is True
    assert is_authorized_telegram(-10042, 7, cfg) is False
