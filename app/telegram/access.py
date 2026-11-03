from __future__ import annotations

import hashlib
from typing import Any

from aiogram.types import CallbackQuery, Message, Update

from app.config import Settings, get_settings
from app.db.models import TelegramInboxPending, TelegramPending


def _parse_admin_chat_id(settings: Settings) -> int | None:
    raw = settings.telegram_admin_chat_id.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_admin_user_id(settings: Settings) -> int | None:
    raw = settings.telegram_admin_user_id.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def is_authorized_telegram(
    chat_id: int | None,
    user_id: int | None,
    settings: Settings | None = None,
) -> bool:
    settings = settings or get_settings()
    expected_chat = _parse_admin_chat_id(settings)
    if expected_chat is None:
        return False
    if chat_id is None or chat_id != expected_chat:
        return False

    expected_user = _parse_admin_user_id(settings)
    if expected_user is None:
        if settings.telegram_admin_user_id.strip():
            return False  # Invalid explicit identity must not fall back to chat-only access.
        # A positive private chat ID identifies its owner; a group ID does not.
        expected_user = expected_chat if expected_chat > 0 else None
    return expected_user is not None and expected_user > 0 and user_id == expected_user


def is_admin_chat_message(message: Message, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    user_id = message.from_user.id if message.from_user else None
    return is_authorized_telegram(message.chat.id, user_id, settings)


def chat_user_from_update(update: Update) -> tuple[int | None, int | None]:
    if update.callback_query:
        callback = update.callback_query
        chat_id = callback.message.chat.id if callback.message else None
        user_id = callback.from_user.id if callback.from_user else None
        return chat_id, user_id
    if update.message:
        user_id = update.message.from_user.id if update.message.from_user else None
        return update.message.chat.id, user_id
    return None, None


def pending_matches_callback(
    pending: TelegramPending | TelegramInboxPending,
    callback: CallbackQuery,
) -> bool:
    if callback.message is None:
        return False
    if pending.chat_id != callback.message.chat.id:
        return False
    return pending.message_id is not None and pending.message_id == callback.message.message_id


def edit_preview_binding(message: Message, content: str) -> dict[str, Any]:
    """Bind the FSM draft to the exact preview that the operator received."""
    return {
        "preview_chat_id": message.chat.id,
        "preview_message_id": message.message_id,
        "preview_content_hash": hashlib.sha256(content.encode()).hexdigest(),
    }


def edit_preview_matches_callback(
    data: dict[str, Any], callback: CallbackQuery, pending_id: str, content: str
) -> bool:
    message = callback.message
    return bool(
        message is not None
        and data.get("pending_id") == pending_id
        and data.get("preview_chat_id") == message.chat.id
        and data.get("preview_message_id") == message.message_id
        and data.get("preview_content_hash") == hashlib.sha256(content.encode()).hexdigest()
    )
