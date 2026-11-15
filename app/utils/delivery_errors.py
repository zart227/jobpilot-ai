"""Shared heuristics for ambiguous network/platform delivery failures."""

from typing import Any

SEND_ATTEMPT_BLOCK_REASONS = frozenset(
    {
        "unknown_result",
        "needs_attention",
        "already_sending",
        "job_already_sent",
        "snapshot_mismatch",
        "idempotency_conflict",
        "new_offer_after_sent",
    }
)


def kwork_steps_include_submit_click(steps: list[Any] | None) -> bool:
    return any(str(step).startswith("нажатие «") for step in (steps or []))


def format_proposal_send_block_message(
    reason: str,
    *,
    last_error: str | None = None,
    debug: dict[str, Any] | None = None,
) -> str:
    """Operator-facing text when a new send is blocked by an in-flight SendAttempt."""
    debug = debug or {}
    steps = debug.get("steps")

    if reason == "unknown_result":
        if kwork_steps_include_submit_click(steps):
            lead = (
                "Предыдущая попытка остановилась после нажатия «Предложить»: "
                "отклик мог уйти на Kwork."
            )
        else:
            lead = (
                "Предыдущая попытка завершилась с неопределённым результатом "
                "(сбой браузера или сети до подтверждения)."
            )
        detail = (
            humanize_kwork_browser_error(last_error)
            or (last_error if last_error and last_error != "unknown_result" else None)
            or debug.get("reason")
        )
        if detail and detail not in lead:
            return f"{lead} {detail}"
        return (
            f"{lead} Проверьте «Мои отклики» на Kwork или повторите отправку после сверки."
        )

    if reason == "already_sending":
        return (
            "Отправка уже выполняется (или застряла в статусе sending). "
            "Подождите 1–2 минуты и нажмите «Повторить». "
            "Если не помогло — перезапустите telegram-bot и попробуйте снова."
        )

    if reason == "needs_attention":
        detail = last_error or debug.get("reason")
        suffix = f" Последняя ошибка: {detail}" if detail else ""
        return (
            "Исчерпаны автоматические повторы отправки; нужны ручная проверка или правка отклика."
            f"{suffix}"
        )

    if reason == "job_already_sent":
        return "По этому заказу уже есть подтверждённая отправка на Kwork."

    if reason == "snapshot_mismatch":
        return "Текст отклика изменился относительно зафиксированной попытки — снова одобрите карточку (EDIT)."

    if reason == "idempotency_conflict":
        return "Конфликт параллельных попыток отправки — повторите через несколько секунд."

    if reason == "new_offer_after_sent":
        return "Нельзя создать новый отклик: по этому заказу отправка уже учтена."

    human = humanize_kwork_browser_error(last_error) if last_error else None
    if human:
        return human
    if last_error and last_error != reason:
        return last_error
    return reason


def format_ambiguous_delivery_outcome(
    error: str | None,
    debug: dict[str, Any] | None = None,
) -> str:
    """Message when finalize stored unknown_result after a live send attempt."""
    debug = debug or {}
    if kwork_steps_include_submit_click(debug.get("steps")):
        base = error or "Отправка могла завершиться на стороне Kwork."
        return (
            f"{base} Исход не подтверждён автоматически — проверьте «Мои отклики» "
            "на Kwork перед повтором из бота."
        )
    human = humanize_kwork_browser_error(error)
    if human:
        return human
    if error:
        return error
    return (
        "Не удалось подтвердить отправку. Повторите — это похоже на временный сбой браузера или сети."
    )


def humanize_kwork_browser_error(error: str | None) -> str | None:
    """Operator-facing summary when Playwright/Chromium errors are not self-explanatory."""
    if not error:
        return None
    lowered = error.lower()
    if "chrome-error://chromewebdata" in lowered:
        return (
            "Браузер не смог загрузить страницу Kwork (временный сбой сети или навигации Chromium). "
            "Это не похоже на закрытый заказ, лимит коннектов или устаревшую сессию. "
            "Повторите отправку — часто срабатывает со второй попытки."
        )
    if "err_timed_out" in lowered or "timeout" in lowered and "page.goto" in lowered:
        return (
            "Таймаут при открытии страницы Kwork — сайт долго не отвечал. "
            "Повторите отправку позже или проверьте доступность kwork.ru из контейнера."
        )
    if "net::err_" in lowered:
        return (
            "Сетевая ошибка браузера при обращении к Kwork (DNS, TLS или обрыв соединения). "
            "Проверьте интернет на хосте и повторите отправку."
        )
    return None


def is_playwright_form_fill_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(
        phrase in lowered
        for phrase in (
            "locator.wait_for",
            "не удалось заполнить форму",
            "поля формы",
            "editor_not_visible",
            "поле названия",
            "поле стоимости",
            "trumbowyg",
        )
    )


def is_ambiguous_send_error(error: str | None) -> bool:
    if not error:
        return False
    if is_playwright_form_fill_error(error):
        return False
    lowered = error.lower()
    markers = (
        "timeout",
        "timed out",
        "неизвест",
        "unknown",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
    )
    return any(marker in lowered for marker in markers)
