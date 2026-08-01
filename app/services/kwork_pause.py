from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from app.config import Settings, get_settings

logger = structlog.get_logger(__name__)

KWORK_PLATFORM = "kwork"

CONNECTS_REPLENISH_RE = re.compile(
    r"коннекты будут пополнены\s+(\d{1,2}\s+[а-яё]+)",
    re.IGNORECASE,
)

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
    detected_at: datetime | None = None
    checked_at: datetime | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "paused_until": self.paused_until.isoformat() if self.paused_until else None,
            "replenish_text": self.replenish_text,
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

        return cls(
            paused=bool(data.get("paused")),
            paused_until=paused_until,
            replenish_text=data.get("replenish_text"),
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
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


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


def is_kwork_paused(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.kwork_pause_enabled:
        return False

    until = resolve_pause_until(settings)
    if until is None:
        state = load_pause_state(settings)
        return bool(state and state.paused)

    today = _local_today(settings)
    if today >= until:
        if load_pause_state(settings):
            clear_pause_state(settings)
            logger.info("JobPilot AI Kwork pause auto-resumed", resume_on=until.isoformat())
        return False
    return True


def kwork_resume_on(settings: Settings | None = None) -> date | None:
    if not is_kwork_paused(settings):
        return None
    return resolve_pause_until(settings)


def format_kwork_pause_reason(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    until = resolve_pause_until(settings)
    until_text = until.strftime("%d.%m.%Y") if until else "?"
    return (
        f"Kwork приостановлен до {until_text} — лимит коннектов исчерпан. "
        "Скрейпинг, генерация откликов и отправка отключены."
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

    age_hours = (datetime.now(timezone.utc) - state.checked_at).total_seconds() / 3600
    return age_hours >= max(1, settings.kwork_pause_check_interval_hours)


def apply_connects_probe_result(
    *,
    limit_exhausted: bool,
    page_text: str = "",
    source: str = "browser",
    confident: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)

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
        clear_pause_state(settings)
        if previous and previous.paused:
            logger.info("JobPilot AI Kwork pause cleared: connects available", source=source)
        return {"paused": False, "cleared": True, "source": source}

    replenish_text = extract_connects_replenish_text(page_text)
    paused_until = extract_pause_until_from_text(page_text, settings)
    previous = load_pause_state(settings)

    state = KworkPauseState(
        paused=True,
        paused_until=paused_until,
        replenish_text=replenish_text,
        detected_at=previous.detected_at if previous and previous.detected_at else now,
        checked_at=now,
        source=source,
    )
    save_pause_state(state, settings)
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
