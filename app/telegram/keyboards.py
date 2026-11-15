from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def platform_send_label(platform: str | None) -> str:
    names = {"kwork": "Kwork", "fl_ru": "FL.ru", "weblancer": "Weblancer"}
    return f"✅ Отправить на {names.get(platform or '', platform or 'площадку')}"


def edit_preview_keyboard(pending_id: str, *, platform: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=platform_send_label(platform),
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


def send_retry_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Повторить отправку",
                    callback_data=f"resend:{pending_id}",
                ),
                InlineKeyboardButton(
                    text="⏭ SKIP",
                    callback_data=f"skip:{pending_id}",
                ),
            ],
        ]
    )


def approval_keyboard(pending_id: str, *, platform: str | None = None) -> InlineKeyboardMarkup:
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


def inbox_reply_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ ОТПРАВИТЬ",
                    callback_data=f"inbox_approve:{pending_id}",
                ),
                InlineKeyboardButton(
                    text="✏️ EDIT",
                    callback_data=f"inbox_edit:{pending_id}",
                ),
                InlineKeyboardButton(
                    text="⏭ SKIP",
                    callback_data=f"inbox_skip:{pending_id}",
                ),
            ],
        ]
    )


def inbox_edit_preview_keyboard(pending_id: str, *, platform: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=platform_send_label(platform),
                    callback_data=f"inbox_edit_send:{pending_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Ещё правки",
                    callback_data=f"inbox_edit_more:{pending_id}",
                ),
                InlineKeyboardButton(
                    text="⏭ Отмена",
                    callback_data=f"inbox_edit_cancel:{pending_id}",
                ),
            ],
        ]
    )
