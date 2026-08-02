from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog

from app.config import Settings

logger = structlog.get_logger(__name__)

OPENAI_API_BASE = "https://api.openai.com"
CREDIT_GRANTS_URL = f"{OPENAI_API_BASE}/dashboard/billing/credit_grants"
PENDING_USAGE_URL = f"{OPENAI_API_BASE}/dashboard/billing/pending_usage"
COSTS_URL = f"{OPENAI_API_BASE}/v1/organization/costs"
USER_AGENT = "JobPilot-AI/1.0 (+https://github.com/jobpilot-ai)"

_cache: dict[str, tuple[float, OpenAIUsageSnapshot]] = {}


@dataclass(frozen=True)
class OpenAIUsageSnapshot:
    credits_granted_usd: float | None = None
    credits_used_usd: float | None = None
    credits_available_usd: float | None = None
    pending_usage_usd: float | None = None
    credits_remaining_usd: float | None = None
    month_spend_usd: float | None = None
    lookback_spend_usd: float | None = None
    budget_usd: float | None = None
    estimated_remaining_usd: float | None = None
    source: Literal["session", "admin", "budget", "none"] = "none"
    error: str | None = None

    def has_data(self) -> bool:
        return any(
            value is not None
            for value in (
                self.credits_remaining_usd,
                self.estimated_remaining_usd,
                self.month_spend_usd,
                self.lookback_spend_usd,
            )
        )

    def remaining_usd(self) -> float | None:
        if self.credits_remaining_usd is not None:
            return self.credits_remaining_usd
        return self.estimated_remaining_usd

    def is_critical(self, threshold_usd: float) -> bool:
        remaining = self.remaining_usd()
        return remaining is not None and remaining <= threshold_usd

    def is_warning(self, warn_usd: float, critical_usd: float) -> bool:
        remaining = self.remaining_usd()
        if remaining is None:
            return False
        return remaining <= warn_usd and remaining > critical_usd


def _session_configured(settings: Settings) -> bool:
    return bool(settings.openai_session_token or settings.openai_session_cookie)


def _billing_headers(settings: Settings) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if settings.openai_session_token:
        headers["Authorization"] = f"Bearer {settings.openai_session_token}"
    if settings.openai_session_cookie:
        headers["Cookie"] = settings.openai_session_cookie
    return headers


def _admin_configured(settings: Settings) -> bool:
    return bool(settings.openai_admin_api_key)


def _budget_configured(settings: Settings) -> bool:
    return settings.openai_budget_usd > 0


def parse_credit_grants(payload: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    granted = payload.get("total_granted")
    used = payload.get("total_used")
    available = payload.get("total_available")
    if granted is None and used is None and available is None:
        return None, None, None
    return (
        float(granted) if granted is not None else None,
        float(used) if used is not None else None,
        float(available) if available is not None else None,
    )


def parse_pending_usage(payload: dict[str, Any]) -> float | None:
    amount = payload.get("amount")
    if amount is None:
        total = payload.get("total_usage")
        return float(total) if total is not None else None
    if isinstance(amount, dict):
        value = amount.get("value")
        return float(value) if value is not None else None
    return float(amount)


def sum_cost_buckets(payload: dict[str, Any]) -> float:
    total = 0.0
    for bucket in payload.get("data", []):
        for result in bucket.get("results", []):
            amount = result.get("amount") or {}
            value = amount.get("value")
            if value is not None:
                total += float(value)
    return total


def _month_start_ts(now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    return int(current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


def _lookback_start_ts(days: int, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()) - max(days - 1, 0) * 86400


def fetch_credits_sync(
    settings: Settings,
    client: httpx.Client | None = None,
) -> tuple[float | None, float | None, float | None, float | None, float | None, str | None]:
    if not _session_configured(settings):
        return None, None, None, None, None, (
            "Задайте OPENAI_SESSION_TOKEN (Bearer из API-запроса к api.openai.com)"
        )
    if not settings.openai_session_token:
        return None, None, None, None, None, (
            "Cookies недостаточно — нужен OPENAI_SESSION_TOKEN из Network → "
            "запрос credit_grants → Authorization: Bearer sess-..."
        )

    owns_client = client is None
    http = client or httpx.Client(timeout=settings.openai_timeout_seconds)
    headers = _billing_headers(settings)
    try:
        grants_response = http.get(CREDIT_GRANTS_URL, headers=headers)
        grants_response.raise_for_status()
        granted, used, available = parse_credit_grants(grants_response.json())

        pending = 0.0
        try:
            pending_response = http.get(PENDING_USAGE_URL, headers=headers)
            pending_response.raise_for_status()
            parsed_pending = parse_pending_usage(pending_response.json())
            pending = parsed_pending if parsed_pending is not None else 0.0
        except Exception as exc:
            logger.warning("OpenAI pending_usage fetch failed", error=str(exc))

        remaining = None
        if available is not None:
            remaining = max(available - pending, 0.0)

        return granted, used, available, pending, remaining, None
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:200]
        if exc.response.status_code in {401, 403}:
            return None, None, None, None, None, (
                "Сессия OpenAI истекла — обновите OPENAI_SESSION_TOKEN из браузера"
            )
        return None, None, None, None, None, f"credit_grants HTTP {exc.response.status_code}: {detail}"
    except Exception as exc:
        logger.warning("OpenAI credit_grants fetch failed", error=str(exc))
        return None, None, None, None, None, str(exc)
    finally:
        if owns_client:
            http.close()


def fetch_costs_sync(
    settings: Settings,
    *,
    start_time: int,
    limit: int = 31,
    client: httpx.Client | None = None,
) -> tuple[float | None, str | None]:
    if not _admin_configured(settings):
        return None, "OPENAI_ADMIN_API_KEY не задан"

    owns_client = client is None
    http = client or httpx.Client(timeout=settings.openai_timeout_seconds)
    headers = {
        "Authorization": f"Bearer {settings.openai_admin_api_key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    total = 0.0
    page: str | None = None
    try:
        while True:
            params: dict[str, Any] = {
                "start_time": start_time,
                "bucket_width": "1d",
                "limit": limit,
            }
            if page:
                params["page"] = page
            response = http.get(COSTS_URL, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
            total += sum_cost_buckets(payload)
            page = payload.get("next_page")
            if not page:
                break
        return total, None
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:200]
        if exc.response.status_code in {401, 403}:
            return None, "Admin key недействителен или нет прав Organization Owner"
        return None, f"costs HTTP {exc.response.status_code}: {detail}"
    except Exception as exc:
        logger.warning("OpenAI costs fetch failed", error=str(exc))
        return None, str(exc)
    finally:
        if owns_client:
            http.close()


def get_openai_usage_sync(
    settings: Settings,
    *,
    force_refresh: bool = False,
) -> OpenAIUsageSnapshot:
    cache_key = (
        settings.openai_session_token[:8]
        if settings.openai_session_token
        else settings.openai_admin_api_key[:8]
        if settings.openai_admin_api_key
        else "no-openai-usage"
    )
    now_mono = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and not force_refresh and now_mono - cached[0] < settings.openai_usage_cache_seconds:
        return cached[1]

    errors: list[str] = []
    source: Literal["session", "admin", "budget", "none"] = "none"

    granted = used = available = pending = remaining = None
    month_spend = lookback_spend = None
    budget = settings.openai_budget_usd if _budget_configured(settings) else None

    with httpx.Client(timeout=settings.openai_timeout_seconds) as client:
        if _session_configured(settings):
            granted, used, available, pending, remaining, credit_error = fetch_credits_sync(
                settings, client
            )
            if credit_error:
                errors.append(credit_error)
            elif remaining is not None:
                source = "session"

        if _admin_configured(settings):
            month_spend, month_error = fetch_costs_sync(
                settings, start_time=_month_start_ts(), client=client
            )
            if month_error:
                errors.append(month_error)
            elif month_spend is not None and source == "none":
                source = "admin"

            lookback_spend, lookback_error = fetch_costs_sync(
                settings,
                start_time=_lookback_start_ts(settings.openai_cost_lookback_days),
                limit=min(settings.openai_cost_lookback_days, 180),
                client=client,
            )
            if lookback_error and lookback_error not in errors:
                errors.append(lookback_error)

    estimated_remaining = None
    if budget is not None and month_spend is not None:
        estimated_remaining = max(budget - month_spend, 0.0)
        if source == "none":
            source = "budget"

    snapshot = OpenAIUsageSnapshot(
        credits_granted_usd=granted,
        credits_used_usd=used,
        credits_available_usd=available,
        pending_usage_usd=pending,
        credits_remaining_usd=remaining,
        month_spend_usd=month_spend,
        lookback_spend_usd=lookback_spend,
        budget_usd=budget,
        estimated_remaining_usd=estimated_remaining,
        source=source,
        error="; ".join(errors) if errors and not any(
            value is not None
            for value in (remaining, estimated_remaining, month_spend, lookback_spend)
        ) else None,
    )
    _cache[cache_key] = (now_mono, snapshot)
    return snapshot


async def get_openai_usage(
    settings: Settings,
    *,
    force_refresh: bool = False,
) -> OpenAIUsageSnapshot:
    import asyncio

    return await asyncio.to_thread(get_openai_usage_sync, settings, force_refresh=force_refresh)


def _format_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def format_usage_summary(snapshot: OpenAIUsageSnapshot) -> str:
    lines: list[str] = []
    if snapshot.credits_remaining_usd is not None:
        lines.append(f"Credits remaining: {_format_usd(snapshot.credits_remaining_usd)}")
        if snapshot.credits_granted_usd is not None:
            lines.append(f"Credits granted: {_format_usd(snapshot.credits_granted_usd)}")
        if snapshot.credits_used_usd is not None:
            lines.append(f"Credits used: {_format_usd(snapshot.credits_used_usd)}")
        if snapshot.pending_usage_usd:
            lines.append(f"Pending usage: {_format_usd(snapshot.pending_usage_usd)}")
    if snapshot.month_spend_usd is not None:
        lines.append(f"Month spend: {_format_usd(snapshot.month_spend_usd)}")
    if snapshot.lookback_spend_usd is not None:
        lines.append(f"Lookback spend: {_format_usd(snapshot.lookback_spend_usd)}")
    if snapshot.budget_usd is not None:
        lines.append(f"Manual budget: {_format_usd(snapshot.budget_usd)}")
    if snapshot.estimated_remaining_usd is not None:
        lines.append(f"Estimated remaining: {_format_usd(snapshot.estimated_remaining_usd)}")
    if snapshot.error and not snapshot.has_data():
        lines.append(f"Unavailable: {snapshot.error}")
    if not lines:
        return (
            "OpenAI usage: no data "
            "(set OPENAI_SESSION_TOKEN and/or OPENAI_ADMIN_API_KEY, optional OPENAI_BUDGET_USD)"
        )
    return "\n".join(lines)


def format_usage_telegram(snapshot: OpenAIUsageSnapshot) -> str:
    parts: list[str] = ["💳 <b>OpenAI — баланс и расход</b>"]
    if snapshot.credits_remaining_usd is not None:
        parts.append(f"Остаток кредитов: <b>{_format_usd(snapshot.credits_remaining_usd)}</b>")
        if snapshot.credits_used_usd is not None:
            parts.append(f"Использовано: {_format_usd(snapshot.credits_used_usd)}")
    if snapshot.month_spend_usd is not None:
        parts.append(f"Расход за месяц: <b>{_format_usd(snapshot.month_spend_usd)}</b>")
    if snapshot.lookback_spend_usd is not None:
        parts.append(f"Расход за период: {_format_usd(snapshot.lookback_spend_usd)}")
    if snapshot.estimated_remaining_usd is not None:
        parts.append(f"Оценка остатка (бюджет): <b>{_format_usd(snapshot.estimated_remaining_usd)}</b>")
    if snapshot.error and not snapshot.has_data():
        parts.append(f"\n<i>Данные недоступны: {snapshot.error}</i>")
    parts.append(
        "\n<a href=\"https://platform.openai.com/settings/organization/billing/overview\">"
        "platform.openai.com/billing</a>"
    )
    return "\n".join(parts)


def _alert_state_path(settings: Settings) -> Path:
    return Path(settings.proxy_state_dir) / "openai_usage_alert.json"


def _should_send_alert(settings: Settings, level: str) -> bool:
    path = _alert_state_path(settings)
    now = datetime.now(UTC).timestamp()
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            last_level = state.get("level")
            last_sent = float(state.get("sent_at", 0))
            if last_level == level and now - last_sent < settings.openai_usage_alert_cooldown_seconds:
                return False
        except Exception:
            pass
    return True


def _mark_alert_sent(settings: Settings, level: str) -> None:
    path = _alert_state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"level": level, "sent_at": datetime.now(UTC).timestamp()}),
        encoding="utf-8",
    )


async def maybe_notify_openai_usage_warning(settings: Settings, bot) -> bool:
    if not settings.telegram_admin_chat_id:
        return False
    if not (_session_configured(settings) or _admin_configured(settings) or _budget_configured(settings)):
        return False

    snapshot = await get_openai_usage(settings)
    if not snapshot.has_data():
        return False

    warn = settings.openai_balance_warn_usd
    critical = settings.openai_balance_critical_usd
    if snapshot.is_critical(critical):
        level = "critical"
    elif snapshot.is_warning(warn, critical):
        level = "warning"
    else:
        return False

    if not _should_send_alert(settings, level):
        return False

    prefix = "🚨" if level == "critical" else "⚠️"
    title = "низкий баланс" if level == "critical" else "баланс заканчивается"
    body = format_usage_telegram(snapshot)
    if body.startswith("💳"):
        body = body.split("\n", 1)[1]
    remaining = snapshot.remaining_usd()
    remaining_line = f"\nОсталось: <b>{_format_usd(remaining)}</b>" if remaining is not None else ""
    text = f"{prefix} <b>OpenAI — {title}</b>{remaining_line}\n\n{body}"
    from aiogram.enums import ParseMode

    await bot.send_message(
        settings.telegram_admin_chat_id,
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    _mark_alert_sent(settings, level)
    logger.info("OpenAI usage alert sent", level=level, remaining=remaining)
    return True
