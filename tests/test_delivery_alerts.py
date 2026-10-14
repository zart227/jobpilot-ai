from app.utils.delivery_alerts import format_inbox_send_failure


def test_inbox_send_failure_mentions_unknown_result_actions() -> None:
    text = format_inbox_send_failure("buyer", None, blocked_reason="unknown_result")
    assert "unknown" in text.lower() or "Неопределённый" in text
    assert "EDIT" in text
