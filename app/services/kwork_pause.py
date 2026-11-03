from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)

KWORK_PLATFORM = "kwork"

CONNECTS_REPLENISH_RE = re.compile(
    r"(?:коннекты будут пополнены|дата пополнения)\s+(\d{1,2}\s+[а-яё]+)",
    re.IGNORECASE | re.MULTILINE,
)
CONNECTS_REMAINING_RE = re.compile(r"осталось\s+(\d+)\s+из\s+\d+", re.IGNORECASE)
CONNECTS_QUOTA_RE = re.compile(r"осталось\s+(\d+)\s+из\s+(\d+)", re.IGNORECASE)

RU_MONTHS: dict[str, int] = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


@dataclass
class KworkPauseState:
    paused: bool
    paused_until: date | None
    replenish_text: str | None = None
    connects_remaining: int | None = None
    connects_total: int | None = None
    detected_at: datetime | None = None
    checked_at: datetime | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "paused_until": self.paused_until.isoformat() if self.paused_until else None,
            "replenish_text": self.replenish_text,
            "connects_remaining": self.connects_remaining,
            "connects_total": self.connects_total,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KworkPauseState:
        paused_until = None
        raw_until = data.get("paused_until")
        if raw_until:
            paused_until = date.fromisoformat(str(raw_until))

        def _parse_dt(value: Any) -> datetime | None:
            if not value:
                return None
            return datetime.fromisoformat(str(value))

        remaining = data.get("connects_remaining")
        total = data.get("connects_total")
        return cls(
            paused=bool(data.get("paused")),
            paused_until=paused_until,
            replenish_text=data.get("replenish_text"),
            connects_remaining=int(remaining) if remaining is not None else None,
            connects_total=int(total) if total is not None else None,
            detected_at=_parse_dt(data.get("detected_at")),
            checked_at=_parse_dt(data.get("checked_at")),
            source=data.get("source"),
        )


def _state_path(settings: Settings) -> Path:
    return Path(settings.kwork_pause_state_file)


def _local_today(settings: Settings) -> date:
    return datetime.now(ZoneInfo(settings.kwork_pause_timezone)).date()


def _manual_pause_until(settings: Settings) -> date | None:
    raw = settings.kwork_pause_until.strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def load_pause_state(settings: Settings | None = None) -> KworkPauseState | None:
    settings = settings or get_settings()
    path = _state_path(settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return KworkPauseState.from_dict(data)
    except Exception as exc:
        logger.warning("Failed to read Kwork pause state", path=str(path), error=str(exc))
        return None


def save_pause_state(state: KworkPauseState, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    path = _state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    except PermissionError as exc:
        logger.error(
            "Failed to write Kwork pause state (fix file ownership, e.g. chown user data/kwork_connects_pause.json)",
            path=str(path),
            error=str(exc),
        )
        raise


def _save_connects_snapshot(
    *,
    page_text: str,
    source: str,
    settings: Settings,
    now: datetime,
    paused: bool = False,
    paused_until: date | None = None,
    replenish_text: str | None = None,
    previous: KworkPauseState | None = None,
) -> KworkPauseState:
    fields = connects_balance_fields(page_text)
    state = KworkPauseState(
        paused=paused,
        paused_until=paused_until,
        replenish_text=replenish_text,
        connects_remaining=fields.get("connects_remaining"),
        connects_total=fields.get("connects_total"),
        detected_at=previous.detected_at if previous and previous.detected_at else now,
        checked_at=now,
        source=source,
    )
    save_pause_state(state, settings)
    return state


def clear_pause_state(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    path = _state_path(settings)
    if path.is_file():
        path.unlink()


def extract_connects_replenish_text(body: str) -> str | None:
    match = CONNECTS_REPLENISH_RE.search(body)
    if not match:
        return None
    return match.group(1).strip()


def extract_connects_remaining(body: str) -> int | None:
    quota = parse_connects_quota(body)
    if quota is not None:
        return quota[0]
    match = CONNECTS_REMAINING_RE.search(body)
    if not match:
        return None
    return int(match.group(1))


def parse_connects_quota(body: str) -> tuple[int, int] | None:
    match = CONNECTS_QUOTA_RE.search(body)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def connects_limit_exhausted(body: str) -> bool:
    """True when Kwork has no connects left for new offers (not mere replenish date widget)."""
    lowered = body.lower()
    if "лимит коннект" in lowered or "исчерпали лимит коннект" in lowered:
        return True
    quota = parse_connects_quota(body)
    if quota is not None:
        return quota[0] <= 0
    return "коннекты будут пополнены" in lowered


def format_connects_balance_line(
    remaining: int | None,
    total: int | None = None,
) -> str:
    if remaining is None:
        return ""
    if total is not None:
        return f"Осталось {remaining} из {total} коннектов"
    return f"Осталось {remaining} коннектов"


def connects_balance_fields(body: str) -> dict[str, int]:
    quota = parse_connects_quota(body)
    if quota is None:
        remaining = extract_connects_remaining(body)
        if remaining is None:
            return {}
        return {"connects_remaining": remaining}
    remaining, total = quota
    return {"connects_remaining": remaining, "connects_total": total}


def get_kwork_connects_status_summary(settings: Settings | None = None) -> str | None:
    state = load_pause_state(settings)
    if not state or state.connects_remaining is None:
        return None
    return format_connects_balance_line(state.connects_remaining, state.connects_total)


def parse_replenish_date_ru(text: str, *, today: date | None = None) -> date | None:
    cleaned = text.strip().lower()
    match = re.match(r"(\d{1,2})\s+([а-яё]+)", cleaned)
    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2)
    month = RU_MONTHS.get(month_name)
    if not month:
        return None

    today = today or date.today()
    year = today.year
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None

    if parsed < today:
        try:
            parsed = date(year + 1, month, day)
        except ValueError:
            return None
    return parsed


def extract_pause_until_from_text(body: str, settings: Settings | None = None) -> date | None:
    settings = settings or get_settings()
    replenish_text = extract_connects_replenish_text(body)
    if not replenish_text:
        return None
    return parse_replenish_date_ru(replenish_text, today=_local_today(settings))


def resolve_pause_until(settings: Settings | None = None) -> date | None:
    settings = settings or get_settings()
    state = load_pause_state(settings)
    if state and state.paused_until:
        return state.paused_until
    return _manual_pause_until(settings)


def is_kwork_connects_pause_active(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.kwork_pause_enabled or not settings.kwork_pause_auto:
        return False
    state = load_pause_state(settings)
    return bool(state and state.paused)


def is_kwork_manual_pause_active(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.kwork_pause_enabled:
        return False
    if not settings.kwork_pause_auto:
        manual_until = _manual_pause_until(settings)
        if manual_until and _local_today(settings) < manual_until:
            return True
    state = load_pause_state(settings)
    if state and state.paused and not settings.kwork_pause_auto:
        return is_kwork_paused(settings)
    return False


def is_kwork_offers_paused(settings: Settings | None = None) -> bool:
    """Pause Kwork scrape, pipeline and proposal delivery (connects or manual)."""
    return is_kwork_paused(settings)


def is_kwork_inbox_paused(settings: Settings | None = None) -> bool:
    """Inbox polling: manual pause always; connects pause only if configured."""
    settings = settings or get_settings()
    if not settings.kwork_pause_enabled:
        return False
    if is_kwork_manual_pause_active(settings):
        return True
    if is_kwork_connects_pause_active(settings):
        return settings.kwork_pause_block_inbox_on_connects
    return False


def is_kwork_paused(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.kwork_pause_enabled:
        return False

    state = load_pause_state(settings)
    if state and state.paused:
        until = state.paused_until
        if until is None:
            return True
        today = _local_today(settings)
        if today < until:
            return True
        if settings.kwork_pause_auto:
            # Replenish date passed but probe has not confirmed availability — keep pause.
            return True
        clear_pause_state(settings)
        logger.info("JobPilot AI Kwork pause auto-resumed", resume_on=until.isoformat())
        return False

    manual_until = _manual_pause_until(settings)
    if manual_until is None:
        return False
    return _local_today(settings) < manual_until


def blocks_kwork_operator_send(settings: Settings | None = None) -> bool:
    """Block operator approve that would call live Kwork; PLATFORM_DRY_RUN always allowed."""
    settings = settings or get_settings()
    if settings.platform_dry_run:
        return False
    return is_kwork_paused(settings)


def kwork_resume_on(settings: Settings | None = None) -> date | None:
    if not is_kwork_paused(settings):
        return None
    return resolve_pause_until(settings)


def format_kwork_pause_reason(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    until = resolve_pause_until(settings)
    until_text = until.strftime("%d.%m.%Y") if until else "?"
    state = load_pause_state(settings)
    balance = ""
    if state and state.connects_remaining is not None:
        balance = f" ({format_connects_balance_line(state.connects_remaining, state.connects_total)})"
    return (
        f"Kwork приостановлен до {until_text} — лимит коннектов исчерпан{balance}. "
        "Поиск новых заданий, генерация откликов и отправка отключены."
    )


def get_kwork_pause_reason(settings: Settings | None = None) -> str | None:
    if not is_kwork_paused(settings):
        return None
    return format_kwork_pause_reason(settings)


def is_kwork_pause_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return "kwork приостановлен" in lowered or "kwork_paused" in lowered


def should_refresh_pause_state(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.kwork_pause_enabled or not settings.kwork_pause_auto:
        return False

    state = load_pause_state(settings)
    if state is None or state.checked_at is None:
        return True

    age_hours = (datetime.now(UTC) - state.checked_at).total_seconds() / 3600
    return age_hours >= max(1, settings.kwork_pause_check_interval_hours)


def apply_connects_probe_result(
    *,
    limit_exhausted: bool,
    page_text: str = "",
    source: str = "browser",
    confident: bool = True,
    connects_available_confirmed: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    now = datetime.now(UTC)

    if not limit_exhausted:
        if not confident:
            previous = load_pause_state(settings)
            if previous:
                previous.checked_at = now
                previous.source = source
                save_pause_state(previous, settings)
                return {
                    "paused": bool(previous.paused),
                    "unchanged": True,
                    "reason": "inconclusive_probe",
                    "source": source,
                }
            manual_until = _manual_pause_until(settings)
            if manual_until and settings.kwork_pause_auto and _local_today(settings) < manual_until:
                bootstrap = KworkPauseState(
                    paused=True,
                    paused_until=manual_until,
                    replenish_text=None,
                    detected_at=now,
                    checked_at=now,
                    source=f"{source}:manual_bootstrap",
                )
                save_pause_state(bootstrap, settings)
                return {
                    "paused": True,
                    "paused_until": manual_until.isoformat(),
                    "bootstrapped": True,
                    "source": source,
                }
            return {
                "paused": False,
                "unchanged": True,
                "reason": "inconclusive_probe",
                "source": source,
            }

        previous = load_pause_state(settings)
        if previous and previous.paused and not connects_available_confirmed:
            previous.checked_at = now
            previous.source = source
            save_pause_state(previous, settings)
            logger.info(
                "JobPilot AI Kwork pause kept: probe inconclusive for resume",
                source=source,
            )
            return {
                "paused": True,
                "unchanged": True,
                "reason": "paused_without_connects_confirmation",
                "source": source,
            }

        _save_connects_snapshot(
            page_text=page_text,
            source=source,
            settings=settings,
            now=now,
            paused=False,
            previous=previous,
        )
        if previous and previous.paused:
            logger.info("JobPilot AI Kwork pause cleared: connects available", source=source)
        fields = connects_balance_fields(page_text)
        return {"paused": False, "cleared": True, "source": source, **fields}

    replenish_text = extract_connects_replenish_text(page_text)
    paused_until = extract_pause_until_from_text(page_text, settings)
    previous = load_pause_state(settings)

    _save_connects_snapshot(
        page_text=page_text,
        source=source,
        settings=settings,
        now=now,
        paused=True,
        paused_until=paused_until,
        replenish_text=replenish_text,
        previous=previous,
    )
    logger.info(
        "JobPilot AI Kwork pause updated from connects probe",
        paused_until=paused_until.isoformat() if paused_until else None,
        replenish_text=replenish_text,
        source=source,
    )
    return {
        "paused": True,
        "paused_until": paused_until.isoformat() if paused_until else None,
        "replenish_text": replenish_text,
        "source": source,
    }


def record_connects_limit_detected(message: str, *, source: str = "send_error") -> None:
    settings = get_settings()
    if not settings.kwork_pause_enabled:
        return
    apply_connects_probe_result(
        limit_exhausted=True,
        page_text=message,
        source=source,
        settings=settings,
    )
