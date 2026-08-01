from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
import structlog

from app.config import Settings

logger = structlog.get_logger(__name__)

SETTINGS_URL = "https://ollama.com/settings"
ME_URL = "https://ollama.com/api/me"
USER_AGENT = "JobPilot-AI/1.0 (+https://github.com/jobpilot-ai)"

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*used", re.IGNORECASE)
_RESET_RE = re.compile(r"Resets in ([^<\"]+)", re.IGNORECASE)
_CLOUDFLARE_MARKERS = ("just a moment", "cf-browser-verification", "cloudflare")

_cache: dict[str, tuple[float, OllamaUsageSnapshot]] = {}


@dataclass(frozen=True)
class UsagePeriod:
    percent: float
    reset_text: str | None = None

    def is_warning(self, threshold: float) -> bool:
        return self.percent >= threshold

    def is_critical(self, threshold: float) -> bool:
        return self.percent >= threshold


@dataclass(frozen=True)
class OllamaUsageSnapshot:
    plan: str | None = None
    email: str | None = None
    session: UsagePeriod | None = None
    weekly: UsagePeriod | None = None
    source: Literal["api", "cookies", "none"] = "none"
    error: str | None = None

    def has_usage(self) -> bool:
        return self.session is not None or self.weekly is not None

    def session_warning(self, threshold: float) -> bool:
        return self.session is not None and self.session.is_warning(threshold)

    def weekly_warning(self, threshold: float) -> bool:
        return self.weekly is not None and self.weekly.is_warning(threshold)

    def session_critical(self, threshold: float) -> bool:
        return self.session is not None and self.session.is_critical(threshold)

    def weekly_critical(self, threshold: float) -> bool:
        return self.weekly is not None and self.weekly.is_critical(threshold)

    def is_critical(self, threshold: float) -> bool:
        return self.session_critical(threshold) or self.weekly_critical(threshold)

    def is_warning(self, warn_threshold: float, critical_threshold: float) -> bool:
        session = self.session_warning(warn_threshold) and not self.session_critical(critical_threshold)
        weekly = self.weekly_warning(warn_threshold) and not self.weekly_critical(critical_threshold)
        return session or weekly or self.is_critical(critical_threshold)


def _cookies_configured(settings: Settings) -> bool:
    return bool(settings.ollama_session_cookie and settings.ollama_aid)


def _build_cookie_header(settings: Settings) -> str:
    parts = [
        f"__Secure-session={settings.ollama_session_cookie}",
        f"aid={settings.ollama_aid}",
    ]
    if settings.ollama_cf_clearance:
        parts.append(f"cf_clearance={settings.ollama_cf_clearance}")
    return "; ".join(parts)


def _percent_after_label(html: str, label: str) -> float | None:
    idx = html.find(label)
    if idx == -1:
        return None
    match = _PERCENT_RE.search(html[idx : idx + 400])
    if not match:
        return None
    return float(match.group(1))


def parse_settings_html(html: str) -> tuple[UsagePeriod | None, UsagePeriod | None, str | None]:
    lowered = html.lower()
    if any(marker in lowered for marker in _CLOUDFLARE_MARKERS):
        return None, None, "Cloudflare challenge — обновите OLLAMA_CF_CLEARANCE из браузера"

    if "cloud usage" not in lowered and "session usage" not in lowered:
        return None, None, "Страница settings не содержит Cloud usage (сессия истекла?)"

    session_pct = _percent_after_label(html, "Session usage")
    weekly_pct = _percent_after_label(html, "Weekly usage")
    resets = [text.strip() for text in _RESET_RE.findall(html)]

    session = (
        UsagePeriod(percent=session_pct, reset_text=resets[0] if resets else None)
        if session_pct is not None
        else None
    )
    weekly = (
        UsagePeriod(
            percent=weekly_pct,
            reset_text=resets[1] if len(resets) > 1 else None,
        )
        if weekly_pct is not None
        else None
    )

    if session is None and weekly is None:
        return None, None, "Не удалось распарсить проценты usage из HTML"

    return session, weekly, None


def fetch_plan_sync(settings: Settings, client: httpx.Client | None = None) -> tuple[str | None, str | None]:
    if not settings.ollama_api_key:
        return None, None

    owns_client = client is None
    http = client or httpx.Client(timeout=settings.ollama_timeout_seconds)
    try:
        response = http.post(
            ME_URL,
            headers={
                "Authorization": f"Bearer {settings.ollama_api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            json={},
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("Plan"), payload.get("Email")
    except Exception as exc:
        logger.warning("Ollama /api/me failed", error=str(exc))
        return None, None
    finally:
        if owns_client:
            http.close()


def fetch_usage_from_settings_sync(
    settings: Settings,
    client: httpx.Client | None = None,
) -> tuple[UsagePeriod | None, UsagePeriod | None, str | None]:
    if not _cookies_configured(settings):
        return None, None, "Cookies не заданы (OLLAMA_SESSION_COOKIE, OLLAMA_AID)"

    owns_client = client is None
    http = client or httpx.Client(timeout=settings.ollama_timeout_seconds, follow_redirects=True)
    try:
        response = http.get(
            SETTINGS_URL,
            headers={
                "Cookie": _build_cookie_header(settings),
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        response.raise_for_status()
        return parse_settings_html(response.text)
    except Exception as exc:
        logger.warning("Ollama settings fetch failed", error=str(exc))
        return None, None, str(exc)
    finally:
        if owns_client:
            http.close()


def get_ollama_usage_sync(
    settings: Settings,
    *,
    force_refresh: bool = False,
) -> OllamaUsageSnapshot:
    cache_key = settings.ollama_session_cookie[:8] if settings.ollama_session_cookie else "no-cookies"
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and not force_refresh and now - cached[0] < settings.ollama_usage_cache_seconds:
        return cached[1]

    with httpx.Client(timeout=settings.ollama_timeout_seconds) as client:
        plan, email = fetch_plan_sync(settings, client)
        session, weekly, error = fetch_usage_from_settings_sync(settings, client)

    source: Literal["api", "cookies", "none"] = "none"
    if session or weekly:
        source = "cookies"
    elif plan:
        source = "api"

    snapshot = OllamaUsageSnapshot(
        plan=plan,
        email=email,
        session=session,
        weekly=weekly,
        source=source,
        error=error,
    )
    _cache[cache_key] = (now, snapshot)
    return snapshot


async def get_ollama_usage(
    settings: Settings,
    *,
    force_refresh: bool = False,
) -> OllamaUsageSnapshot:
    import asyncio

    return await asyncio.to_thread(get_ollama_usage_sync, settings, force_refresh=force_refresh)


def format_usage_summary(snapshot: OllamaUsageSnapshot) -> str:
    lines: list[str] = []
    if snapshot.plan:
        lines.append(f"Plan: {snapshot.plan}")
    if snapshot.email:
        lines.append(f"Account: {snapshot.email}")
    if snapshot.session:
        reset = f", reset {snapshot.session.reset_text}" if snapshot.session.reset_text else ""
        lines.append(f"Session: {snapshot.session.percent:.1f}% used{reset}")
    if snapshot.weekly:
        reset = f", reset {snapshot.weekly.reset_text}" if snapshot.weekly.reset_text else ""
        lines.append(f"Weekly: {snapshot.weekly.percent:.1f}% used{reset}")
    if snapshot.error and not snapshot.has_usage():
        lines.append(f"Usage unavailable: {snapshot.error}")
    if not lines:
        return "Ollama usage: no data (set OLLAMA_SESSION_COOKIE + OLLAMA_AID)"
    return "\n".join(lines)


def format_usage_telegram(snapshot: OllamaUsageSnapshot) -> str:
    parts: list[str] = ["☁️ <b>Ollama Cloud — лимиты</b>"]
    if snapshot.plan:
        parts.append(f"План: <b>{snapshot.plan}</b>")
    if snapshot.session:
        reset = f" (сброс: {snapshot.session.reset_text})" if snapshot.session.reset_text else ""
        parts.append(f"Session: <b>{snapshot.session.percent:.1f}%</b>{reset}")
    if snapshot.weekly:
        reset = f" (сброс: {snapshot.weekly.reset_text})" if snapshot.weekly.reset_text else ""
        parts.append(f"Weekly: <b>{snapshot.weekly.percent:.1f}%</b>{reset}")
    if snapshot.error and not snapshot.has_usage():
        parts.append(f"\n<i>Usage недоступен: {snapshot.error}</i>")
    parts.append("\n<a href=\"https://ollama.com/settings\">ollama.com/settings</a>")
    return "\n".join(parts)


def _alert_state_path(settings: Settings) -> Path:
    return Path(settings.proxy_state_dir) / "ollama_usage_alert.json"


def _should_send_alert(settings: Settings, level: str) -> bool:
    path = _alert_state_path(settings)
    now = datetime.now(UTC).timestamp()
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            last_level = state.get("level")
            last_sent = float(state.get("sent_at", 0))
            if last_level == level and now - last_sent < settings.ollama_usage_alert_cooldown_seconds:
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


async def maybe_notify_ollama_usage_warning(settings: Settings, bot) -> bool:
    if not settings.telegram_admin_chat_id:
        return False
    if not _cookies_configured(settings) and not settings.ollama_api_key:
        return False

    snapshot = await get_ollama_usage(settings)
    if not snapshot.has_usage():
        return False

    warn = settings.ollama_usage_warn_percent
    critical = settings.ollama_usage_critical_percent
    if snapshot.is_critical(critical):
        level = "critical"
    elif snapshot.is_warning(warn, critical):
        level = "warning"
    else:
        return False

    if not _should_send_alert(settings, level):
        return False

    prefix = "🚨" if level == "critical" else "⚠️"
    title = "критический уровень" if level == "critical" else "высокий уровень"
    body = format_usage_telegram(snapshot)
    if body.startswith("☁️"):
        body = body.split("\n", 1)[1]
    text = f"{prefix} <b>Ollama Cloud — {title}</b>\n\n{body}"
    from aiogram.enums import ParseMode

    await bot.send_message(
        settings.telegram_admin_chat_id,
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    _mark_alert_sent(settings, level)
    logger.info("Ollama usage alert sent", level=level, session=snapshot.session, weekly=snapshot.weekly)
    return True
