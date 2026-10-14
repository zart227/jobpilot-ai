from __future__ import annotations

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
        return True
    return user_id is not None and user_id == expected_user


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
    return not (pending.message_id is not None and pending.message_id != callback.message.message_id)
