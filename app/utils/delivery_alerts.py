"""User-facing Telegram text for delivery uncertainty and inbox errors."""

import html


def format_inbox_send_failure(
    username: str,
    error: str | None,
    *,
    blocked_reason: str | None = None,
) -> str:
    lines = [
        f"❌ <b>Не удалось отправить ответ</b> клиенту {html.escape(username)}.",
    ]
    detail = error or blocked_reason
    if detail:
        lines.append(f"\n<b>Причина:</b> {html.escape(detail)}")
    reason_lower = (blocked_reason or error or "").lower()
    if "unknown" in reason_lower or reason_lower == "send_blocked":
        lines.append(
            "\n\n⚠️ <b>Неопределённый результат</b>: сообщение могло уйти на Kwork. "
            "Проверьте переписку вручную перед повторной отправкой."
        )
        lines.append(
            "\n<b>Действия:</b> при необходимости EDIT с новым текстом; "
            "при сомнении — SKIP и сверка по runbook (unknown_result)."
        )
    else:
        lines.append("\n<b>Действия:</b> EDIT — исправить текст; повторить отправку после правки.")
    return "\n".join(lines)
