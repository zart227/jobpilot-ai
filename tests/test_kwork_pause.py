from datetime import UTC, date, datetime

import pytest

from app.config import Settings

FIXED_LOCAL_TODAY = date(2026, 7, 7)


@pytest.fixture(autouse=True)
def freeze_kwork_local_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.kwork_pause._local_today",
        lambda _settings: FIXED_LOCAL_TODAY,
    )
from app.services.kwork_pause import (
    KworkPauseState,
    apply_connects_probe_result,
    blocks_kwork_operator_send,
    connects_limit_exhausted,
    extract_connects_replenish_text,
    format_kwork_pause_reason,
    is_kwork_paused,
    kwork_resume_on,
    load_pause_state,
    parse_connects_quota,
    parse_replenish_date_ru,
    save_pause_state,
)


def test_parse_replenish_date_ru() -> None:
    assert parse_replenish_date_ru("10 июля", today=date(2026, 7, 7)) == date(2026, 7, 10)
    assert parse_replenish_date_ru("10 января", today=date(2026, 7, 7)) == date(2027, 1, 10)


def test_extract_replenish_text() -> None:
    text = "Важно! Ваши коннекты будут пополнены 10 июля."
    assert extract_connects_replenish_text(text) == "10 июля"
    listing = "Коннекты\nОсталось 0 из 30\nДата пополнения\n10 августа"
    assert extract_connects_replenish_text(listing) == "10 августа"


def test_extract_connects_remaining() -> None:
    from app.services.kwork_pause import extract_connects_remaining

    assert extract_connects_remaining("Осталось 0 из 30") == 0
    assert extract_connects_remaining("Осталось 5 из 30") == 5
    assert extract_connects_remaining("no connects here") is None


def test_parse_connects_quota() -> None:
    assert parse_connects_quota("Осталось 29 из 30") == (29, 30)


def test_replenish_date_widget_does_not_exhaust_connects() -> None:
    widget = "Коннекты\nОсталось 29 из 30\nДата пополнения\n10 ноября"
    assert connects_limit_exhausted(widget) is False
    assert parse_connects_quota(widget) == (29, 30)


def test_zero_connects_with_replenish_date_is_exhausted() -> None:
    listing = "Коннекты\nОсталось 0 из 30\nДата пополнения\n10 августа"
    assert connects_limit_exhausted(listing) is True


def test_probe_clears_pause_with_quota_page_text(tmp_path) -> None:
    state_file = tmp_path / "pause.json"
    settings = Settings(
        _env_file=None,
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=True,
        KWORK_PAUSE_UNTIL="",
        KWORK_PAUSE_STATE_FILE=str(state_file),
    )
    save_pause_state(
        KworkPauseState(paused=True, paused_until=date(2026, 11, 10)),
        settings,
    )
    result = apply_connects_probe_result(
        limit_exhausted=False,
        page_text="Коннекты\nОсталось 29 из 30\nДата пополнения\n10 ноября",
        connects_available_confirmed=True,
        settings=settings,
    )
    assert result["paused"] is False
    assert result["connects_remaining"] == 29
    assert result["connects_total"] == 30
    assert is_kwork_paused(settings) is False
    state = load_pause_state(settings)
    assert state is not None
    assert state.paused is False
    assert state.connects_remaining == 29


def test_format_kwork_success_message_includes_connects() -> None:
    from app.telegram.bot import format_kwork_success_message

    msg = format_kwork_success_message(
        {"connects_remaining": 28, "connects_total": 30},
    )
    assert "28" in msg
    assert "30" in msg
    assert "коннект" in msg.lower()


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
        _env_file=None,
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=True,
        KWORK_PAUSE_UNTIL="",
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
            checked_at=datetime.now(UTC),
        ),
        settings,
    )
    assert is_kwork_paused(settings) is True
    apply_connects_probe_result(
        limit_exhausted=False,
        page_text="У вас 5 коннектов",
        connects_available_confirmed=True,
        settings=settings,
    )
    assert is_kwork_paused(settings) is False
    state = load_pause_state(settings)
    assert state is not None
    assert state.paused is False


def test_probe_clears_pause_when_connects_available(tmp_path) -> None:
    state_file = tmp_path / "pause.json"
    settings = Settings(
        _env_file=None,
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=True,
        KWORK_PAUSE_UNTIL="",
        KWORK_PAUSE_STATE_FILE=str(state_file),
    )
    save_pause_state(
        KworkPauseState(paused=True, paused_until=date(2026, 7, 10)),
        settings,
    )
    apply_connects_probe_result(
        limit_exhausted=False,
        page_text="У вас 5 коннектов",
        connects_available_confirmed=True,
        settings=settings,
    )
    state = load_pause_state(settings)
    assert state is not None
    assert state.paused is False
    assert is_kwork_paused(settings) is False


def test_probe_keeps_pause_without_connects_confirmation(tmp_path) -> None:
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
    result = apply_connects_probe_result(
        limit_exhausted=False,
        confident=True,
        connects_available_confirmed=False,
        settings=settings,
    )
    assert result["unchanged"] is True
    assert result["reason"] == "paused_without_connects_confirmation"
    assert is_kwork_paused(settings) is True


def test_manual_pause_not_active_after_resume_date(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "pause.json"
    settings = Settings(
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=False,
        KWORK_PAUSE_UNTIL="2026-07-10",
        KWORK_PAUSE_TIMEZONE="Europe/Moscow",
        KWORK_PAUSE_STATE_FILE=str(state_file),
    )
    monkeypatch.setattr(
        "app.services.kwork_pause._local_today",
        lambda _settings: date(2026, 7, 11),
    )
    assert is_kwork_paused(settings) is False


def test_manual_pause_active_on_day_before_resume(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / "pause.json"
    settings = Settings(
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_AUTO=False,
        KWORK_PAUSE_UNTIL="2026-07-10",
        KWORK_PAUSE_TIMEZONE="Europe/Moscow",
        KWORK_PAUSE_STATE_FILE=str(state_file),
    )
    monkeypatch.setattr(
        "app.services.kwork_pause._local_today",
        lambda _settings: date(2026, 7, 9),
    )
    assert is_kwork_paused(settings) is True


def test_kwork_pause_disabled(tmp_path) -> None:
    settings = Settings(
        KWORK_PAUSE_ENABLED=False,
        KWORK_PAUSE_UNTIL="2099-01-01",
        KWORK_PAUSE_STATE_FILE=str(tmp_path / "pause.json"),
    )
    assert is_kwork_paused(settings) is False


def test_blocks_kwork_operator_send_allows_dry_run(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        PLATFORM_DRY_RUN=True,
        KWORK_PAUSE_ENABLED=True,
        KWORK_PAUSE_UNTIL="2099-01-01",
        KWORK_PAUSE_STATE_FILE=str(tmp_path / "pause.json"),
    )
    assert is_kwork_paused(settings) is True
    assert blocks_kwork_operator_send(settings) is False
