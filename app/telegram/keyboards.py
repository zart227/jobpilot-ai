from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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
