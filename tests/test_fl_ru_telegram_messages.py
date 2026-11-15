from app.services.fl_ru_browser import FL_RU_ORDER_CLOSED_ERROR, FL_RU_PAID_OFFERS_ERROR
from app.telegram.bot import (
    format_fl_ru_failure_message,
    format_send_outcome_message,
    format_weblancer_failure_message,
    _send_outcome_is_order_closed,
)


def test_send_outcome_routes_fl_ru_to_fl_message() -> None:
    body = format_send_outcome_message(
        None,
        success=False,
        send_error=FL_RU_PAID_OFFERS_ERROR,
        send_debug={"platform": "fl_ru", "error_label": "fl_ru_paid_offers_required"},
    )
    assert "FL.ru" in body
    assert "Kwork" not in body
    assert "Частые причины" not in body
    assert "fl.ru/payed" in body


def test_send_outcome_fl_ru_order_closed_message() -> None:
    body = format_send_outcome_message(
        None,
        success=False,
        send_error=FL_RU_ORDER_CLOSED_ERROR,
        send_debug={
            "platform": "fl_ru",
            "error_label": "order_closed",
            "job_url": "https://www.fl.ru/projects/5528077/foo.html",
            "job_title": "верстка и счетчик метрики",
        },
    )
    assert "FL.ru уже закрыт" in body
    assert "Kwork" not in body
    assert "Кнопка «Откликнуться»" not in body
    assert _send_outcome_is_order_closed(FL_RU_ORDER_CLOSED_ERROR, {"error_label": "order_closed"})


def test_send_outcome_weblancer_order_closed() -> None:
    from app.services.weblancer_send import WEBLANCER_ORDER_CLOSED_ERROR
    from app.telegram.bot import format_send_outcome_message

    msg = format_send_outcome_message(
        None,
        success=False,
        send_error=WEBLANCER_ORDER_CLOSED_ERROR,
        send_debug={"platform": "weblancer", "error_label": "order_closed"},
    )
    assert "Weblancer уже закрыт" in msg
    assert "Kwork" not in msg


def test_send_outcome_routes_weblancer_not_kwork() -> None:
    body = format_send_outcome_message(
        None,
        success=False,
        send_error="Автоотправка на weblancer пока не поддерживается",
        send_debug={
            "platform": "weblancer",
            "error_label": "platform_sender_missing",
            "job_url": "https://www.weblancer.net/freelance/html-verstka-32/foo-1267587/",
        },
    )
    assert "Weblancer" in body
    assert "Kwork" not in body
    assert "Частые причины" not in body
    assert "telegram-bot" in body


def test_weblancer_failure_shows_weblancer_link() -> None:
    text = format_weblancer_failure_message(
        None,
        "test",
        {
            "job_url": "https://www.weblancer.net/freelance/foo-1203726/",
            "platform": "weblancer",
        },
    )
    assert "Открыть на Weblancer" in text


def test_fl_ru_failure_shows_fl_link() -> None:
    text = format_fl_ru_failure_message(
        None,
        "test",
        {"job_url": "https://www.fl.ru/projects/5528401/foo.html", "platform": "fl_ru"},
    )
    assert "Открыть на FL.ru" in text
