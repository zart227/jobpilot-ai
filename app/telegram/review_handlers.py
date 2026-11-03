"""Explicit operator re-review; never approve or send to the marketplace here."""

import uuid

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.services.review_reissue import reissue_review, review_reference_for_message
from app.telegram.access import is_admin_chat_message

USAGE = (
    "Ответьте /rereview на старую карточку. Для потерянной карточки: "
    "/rereview proposal <pending UUID> или /rereview inbox <pending UUID>."
)
REASONS = {
    "unauthorized": "Нет доступа.",
    "not_found": "Карточка не найдена.",
    "chat_mismatch": "Карточка принадлежит другому чату.",
    "stale_card": "ID исходной карточки изменился.",
    "protected_status": "Карточка уже закрыта или заменена.",
    "already_sent_or_protected": "Отклик уже отправлен или защищён от изменения.",
    "delivery_requires_resolution": "Отправка выполняется, уже подтверждена или требует сверки. Сначала разрешите её состояние.",
    "invalid_target": "Связь карточки с заказом повреждена.",
}


def register_review_handlers(router: Router) -> None:
    @router.message(Command("rereview"))
    async def handle_rereview(message: Message, state: FSMContext | None = None) -> None:
        if not is_admin_chat_message(message):
            await message.answer("Нет доступа.")
            return
        args = (message.text or "").split()
        source_message_id = None
        if len(args) == 1 and message.reply_to_message:
            source_message_id = message.reply_to_message.message_id
            reference = await review_reference_for_message(
                message.chat.id,
                source_message_id,
                user_id=message.from_user.id,
            )
            if reference is None:
                await message.answer("Не найдена однозначная карточка в этом чате. " + USAGE)
                return
            kind, pending_id = reference
        elif len(args) == 3 and args[1] in ("proposal", "inbox"):
            kind = args[1]
            try:
                pending_id = uuid.UUID(args[2])
            except ValueError:
                await message.answer(USAGE)
                return
        else:
            await message.answer(USAGE)
            return
        result = await reissue_review(
            kind,
            pending_id,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            source_message_id=source_message_id,
        )
        if result.action == "blocked":
            await message.answer(REASONS.get(result.reason, "Повторный показ заблокирован."))
            return
        if state is not None:
            data = await state.get_data()
            if data.get("pending_id") == str(pending_id):
                await state.clear()
        label = (
            "Новая карточка поставлена в очередь"
            if result.action == "queued"
            else "Карточка уже была поставлена в очередь"
        )
        explanation = (
            "Нужно новое одобрение; отправка клиенту не запускается."
            if result.action == "queued"
            else "Повторный запрос ничего не отправляет. Проверьте состояние указанной карточки."
        )
        await message.answer(f"{label}: {result.pending_id}.\n{explanation}")
