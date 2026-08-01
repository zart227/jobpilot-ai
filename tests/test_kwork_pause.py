from datetime import date, datetime, timezone

from app.config import Settings
from app.services.kwork_pause import (
    KworkPauseState,
    apply_connects_probe_result,
    extract_connects_replenish_text,
    format_kwork_pause_reason,
    is_kwork_paused,
    kwork_resume_on,
    load_pause_state,
    parse_replenish_date_ru,
    save_pause_state,
    clear_pause_state,
)


def test_parse_replenish_date_ru() -> None:
    assert parse_replenish_date_ru("10 июля", today=date(2026, 7, 7)) == date(2026, 7, 10)
    assert parse_replenish_date_ru("10 января", today=date(2026, 7, 7)) == date(2027, 1, 10)


def test_extract_replenish_text() -> None:
    text = "Важно! Ваши коннекты будут пополнены 10 июля."
    assert extract_connects_replenish_text(text) == "10 июля"


def test_manual_pause_bootstrap_without_state_file(tmp_path) -> None:
    state_file = tmp_path / "pause.json"
    settings = Settings(
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=True,
        KWORK_PAUSE_UNTIL="2026-07-10",
        KWORK_PAUSE_TIMEZONE="Europe/Moscow",
        KWORK_PAUSE_STATE_FILE=str(state_file),
    )
    assert is_kwork_paused(settings) is True
    assert kwork_resume_on(settings) == date(2026, 7, 10)


def test_manual_pause_before_resume_date(tmp_path) -> None:
    state_file = tmp_path / "pause.json"
    settings = Settings(
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=False,
        KWORK_PAUSE_UNTIL="2026-07-10",
        KWORK_PAUSE_TIMEZONE="Europe/Moscow",
        KWORK_PAUSE_STATE_FILE=str(state_file),
    )
    assert is_kwork_paused(settings) is True
    assert kwork_resume_on(settings) == date(2026, 7, 10)
    assert "10.07.2026" in format_kwork_pause_reason(settings)


def test_auto_pause_from_probe_and_resume(tmp_path) -> None:
    state_file = tmp_path / "pause.json"
    settings = Settings(
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=True,
        KWORK_PAUSE_TIMEZONE="Europe/Moscow",
        KWORK_PAUSE_STATE_FILE=str(state_file),
        KWORK_PAUSE_CHECK_INTERVAL_HOURS=1,
    )

    apply_connects_probe_result(
        limit_exhausted=True,
        page_text="Ваши коннекты будут пополнены 10 июля.",
        source="test",
        settings=settings,
    )
    assert is_kwork_paused(settings) is True
    assert load_pause_state(settings) is not None
    assert load_pause_state(settings).paused_until == date(2026, 7, 10)

    save_pause_state(
        KworkPauseState(
            paused=True,
            paused_until=date(2026, 7, 7),
            checked_at=datetime.now(timezone.utc),
        ),
        settings,
    )
    assert is_kwork_paused(settings) is False
    assert load_pause_state(settings) is None


def test_probe_clears_pause_when_connects_available(tmp_path) -> None:
    state_file = tmp_path / "pause.json"
    settings = Settings(
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=True,
        KWORK_PAUSE_STATE_FILE=str(state_file),
    )
    save_pause_state(
        KworkPauseState(paused=True, paused_until=date(2026, 7, 10)),
        settings,
    )
    apply_connects_probe_result(limit_exhausted=False, settings=settings)
    assert load_pause_state(settings) is None
    assert is_kwork_paused(settings) is False


def test_kwork_pause_disabled(tmp_path) -> None:
    settings = Settings(
        KWORK_PAUSE_ENABLED=False,
        KWORK_PAUSE_UNTIL="2099-01-01",
        KWORK_PAUSE_STATE_FILE=str(tmp_path / "pause.json"),
    )
    assert is_kwork_paused(settings) is False
