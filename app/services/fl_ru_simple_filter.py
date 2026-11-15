"""Heuristics to keep FL.ru scrape focused on small, bounded tasks."""

from __future__ import annotations

import re

_COMPLEX_PHRASES = (
    "под ключ",
    "с нуля",
    "архитектор",
    "enterprise",
    "микросервис",
    "highload",
    "high load",
    "долгосроч",
    "постоянн",
    "full-time",
    "full time",
    "фултайм",
    "в штат",
    "команда разработ",
    "целая команда",
    "экосистем",
    "масштабир",
    "переписать проект",
    "разработать платформу",
    "маркетплейс с нуля",
    "crm с нуля",
    "стартап с нуля",
    "senior",
    "тимлид",
    "lead ",
    " cto",
)


def _normalized_text(title: str, description: str) -> str:
    return f"{title}\n{description}".lower().replace("\xa0", " ")


def fl_ru_listing_html_is_free_for_all(card_html: str) -> bool:
    """True when listing card shows the visible «Для всех» badge (free response).

    Tooltip copy uses lowercase «для всех» inside data-tip-txt; the badge label is «Для всех».
    """
    return "Для всех" in card_html


def fl_ru_listing_reject_reason(
    *,
    title: str,
    description: str,
    budget_max: float | None = None,
    max_budget_rub: float | None = None,
) -> str | None:
    text = _normalized_text(title, description)
    for phrase in _COMPLEX_PHRASES:
        if phrase in text:
            return f"FL.ru: похоже на крупный/долгий проект («{phrase}»)"

    if max_budget_rub is not None and budget_max is not None and budget_max > max_budget_rub:
        return f"FL.ru: бюджет выше лимита простых заказов ({int(max_budget_rub)} ₽)"

    # Very long briefs rarely fit a same-day micro-task.
    if len(description) > 6000:
        return "FL.ru: слишком объёмное описание для простого заказа"

    return None


def fl_ru_title_budget_quick_reject(title: str, budget_text: str) -> str | None:
    """Reject obvious non-dev or microcopy spam before detail fetch."""
    t = title.lower()
    spam = (
        "написание отзыв",
        "расшифров",
        "обзвон",
        "курьер",
        "уборк",
    )
    if any(s in t for s in spam):
        return "FL.ru: не программирование"
    if re.search(r"отзыв", t) and "сайт" not in t and "api" not in t:
        return "FL.ru: не программирование"
    return None
