"""Heuristic filter for Kwork jobs that require seller portfolio in the category."""

from __future__ import annotations

import re
from typing import Any

from app.config import get_settings

# Subcategories where Kwork typically blocks offers without portfolio kworks.
_PORTFOLIO_REQUIRED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:создать|разработать|сделать|сверстать|собрать)\s+(?:новый\s+)?(?:сайт|лендинг|landing)\b",
        r"\b(?:новый|копия)\s+сайт\b",
        r"\bинтернет[-\s]?магазин\b",
        r"\b(?:online|e-)?commerce\b",
        r"\bmarketplace\s+mvp\b",
        r"\b(?:web|веб)[-\s]?приложени",
        r"\b(?:мобильн(?:ое|ого|ые)?\s+)?приложени(?:е|я)\b.*\b(?:ios|android|react\s+native|flutter)\b",
        r"\b(?:ios|android)\s+приложени",
        r"\breact\s*(?:\+|/|\s)\s*next",
        r"\bfigma[-\s]?макет\b.*\b(?:сайт|магазин|shop|store)\b",
        r"\b(?:сайт|магазин).*\bfigma[-\s]?макет\b",
        r"\b(?:crm|erp)\b.*\b(?:разработ|создан|с нуля)\b",
        r"\b(?:разработ|создан).*\b(?:crm|erp)\b",
        r"\bsaas\b.*\b(?:платформ|сервис|продукт|разработ)\b",
        r"\b(?:платформ|сервис).*\bsaas\b",
    )
)

# Jobs in these niches usually respond without portfolio kworks (scripts/bots/parsers).
_PORTFOLIO_ALLOWED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\btelegram\b",
        r"\bтг[-\s]?бот\b",
        r"\bчат[-\s]?бот\b",
        r"\bchatbot\b",
        r"\b(?:парсер|парсинг|parser|scraping)\b",
        r"\b(?:скрипт|script)\b",
        r"\b(?:ии|ai)[-\s]?(?:бот|агент|agent)\b",
        r"\b(?:bot|бот)\b",
        r"\b(?:интеграци|integration)\b",
        r"\bapi\b",
        r"\b(?:доработ|fix|исправ|bug|баг)\b",
        r"\b(?:консультаци|audit|аудит)\b",
        r"\b(?:уведомлен|alert|алерт)\b",
        r"\b(?:настройк|setup|config)\b",
        r"\b(?:a-parser|aparser)\b",
    )
)


def _job_text(job: dict[str, Any]) -> str:
    parts = [str(job.get("title", "")), str(job.get("description", ""))]
    skills = job.get("skills") or []
    if skills:
        parts.append(" ".join(str(skill) for skill in skills))
    return " ".join(parts)


def job_requires_kwork_portfolio(job: dict[str, Any]) -> tuple[bool, str]:
    """Return True when a Kwork job likely needs portfolio to submit an offer."""
    settings = get_settings()
    if not settings.kwork_skip_portfolio_jobs:
        return False, ""

    if job.get("platform") != "kwork":
        return False, ""

    text = _job_text(job)
    if not text.strip():
        return False, ""

    for pattern in _PORTFOLIO_ALLOWED_PATTERNS:
        if pattern.search(text):
            return False, ""

    for pattern in _PORTFOLIO_REQUIRED_PATTERNS:
        if pattern.search(text):
            return True, (
                "Kwork требует портфолио в рубрике (сайты/SaaS/приложения), "
                "отклик недоступен без кворка с работами"
            )

    return False, ""
