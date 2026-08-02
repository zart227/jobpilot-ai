from app.services.kwork_browser import (
    KWORK_ORDER_CLOSED_ERROR,
    _order_closed_in_body,
    is_kwork_order_closed_error,
)


def test_order_closed_in_body_detects_project_already_closed() -> None:
    body = "К сожалению, проект уже закрыт. Смотрите похожие проекты на Бирже"
    assert _order_closed_in_body(body) is True


def test_order_closed_in_body_detects_executor_chosen() -> None:
    body = "Исполнитель уже выбран, отклики не принимаются"
    assert _order_closed_in_body(body) is True


def test_order_closed_in_body_open_project() -> None:
    body = "Предложить услугу за 3500 руб."
    assert _order_closed_in_body(body) is False


def test_is_kwork_order_closed_error_matches_closed_messages() -> None:
    assert is_kwork_order_closed_error(KWORK_ORDER_CLOSED_ERROR) is True
    assert is_kwork_order_closed_error("Заказ закрыт или в архиве на Kwork") is True
    assert (
        is_kwork_order_closed_error(
            "Кнопка «Предложить услугу» отсутствует — форма отклика недоступна на Kwork"
        )
        is False
    )
