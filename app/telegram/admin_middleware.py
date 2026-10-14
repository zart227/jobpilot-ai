from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.telegram.access import chat_user_from_update, is_authorized_telegram


class TelegramAdminMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        chat_id, user_id = chat_user_from_update(event)
        if is_authorized_telegram(chat_id, user_id):
            return await handler(event, data)

        if event.callback_query:
            await event.callback_query.answer("Нет доступа", show_alert=True)
            return None
        if event.message:
            await event.message.answer("Доступ запрещён.")
            return None
        return None
