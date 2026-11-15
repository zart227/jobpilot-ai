from types import SimpleNamespace

from app.telegram.bot import _pending_allows_send_retry, _send_outcome_is_order_closed


def test_retry_allowed_only_for_approved_or_edited() -> None:
    assert _pending_allows_send_retry(SimpleNamespace(status="approved")) is True
    assert _pending_allows_send_retry(SimpleNamespace(status="edited")) is True
    assert _pending_allows_send_retry(SimpleNamespace(status="skipped")) is False
    assert _pending_allows_send_retry(SimpleNamespace(status="pending")) is False


def test_order_closed_detected_from_send_debug() -> None:
    assert _send_outcome_is_order_closed(
        None,
        {
            "error_label": "order_closed",
            "reason": "Заказ закрыт на Kwork — исполнитель уже выбран",
        },
    )
