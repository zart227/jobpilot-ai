from app.services.kwork_browser import KWORK_ORDER_CLOSED_ERROR
from app.telegram.bot import (
    format_kwork_order_closed_message,
    format_send_outcome_message,
    _send_outcome_is_order_closed,
)


def test_send_outcome_detects_order_closed_by_label() -> None:
    assert _send_outcome_is_order_closed(
        None,
        {"error_label": "order_closed", "reason": "other"},
    )


def test_order_closed_message_is_actionable_without_generic_failure_footer() -> None:
    text = format_kwork_order_closed_message(
        None,
        KWORK_ORDER_CLOSED_ERROR,
        {"job_title": "Fix checkboxes", "job_url": "https://kwork.ru/projects/1"},
    )
    assert "уже закрыт" in text
    assert "SKIP" in text
    assert "Частые причины" not in text
    assert "не ошибка сессии" in text


def test_format_send_outcome_routes_order_closed() -> None:
    body = format_send_outcome_message(
        None,
        success=False,
        send_error=KWORK_ORDER_CLOSED_ERROR,
        send_debug={"error_label": "order_closed"},
    )
    assert "уже закрыт" in body
    assert body.startswith("🚫")
