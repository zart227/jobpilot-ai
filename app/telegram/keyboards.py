from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def edit_preview_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отправить на Kwork",
                    callback_data=f"edit_send:{pending_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Ещё правки",
                    callback_data=f"edit_more:{pending_id}",
                ),
                InlineKeyboardButton(
                    text="⏭ Отмена",
                    callback_data=f"edit_cancel:{pending_id}",
                ),
            ],
        ]
    )


def approval_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ APPROVE",
                    callback_data=f"approve:{pending_id}",
                ),
                InlineKeyboardButton(
                    text="✏️ EDIT",
                    callback_data=f"edit:{pending_id}",
                ),
                InlineKeyboardButton(
                    text="⏭ SKIP",
                    callback_data=f"skip:{pending_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📋 Задание",
                    callback_data=f"view_job:{pending_id}",
                ),
            ],
        ]
    )
