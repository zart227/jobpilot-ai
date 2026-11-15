from app.utils.delivery_errors import (
    format_ambiguous_delivery_outcome,
    format_proposal_send_block_message,
)


def test_block_message_unknown_result_not_raw_token() -> None:
    text = format_proposal_send_block_message(
        "unknown_result",
        last_error="Page.goto: chrome-error://chromewebdata/",
        debug={"error_label": "exception"},
    )
    assert "unknown_result" not in text
    assert "Kwork" in text or "браузер" in text.lower()


def test_block_message_already_sending() -> None:
    text = format_proposal_send_block_message("already_sending")
    assert "уже выполняется" in text.lower()


def test_ambiguous_outcome_after_submit_click() -> None:
    text = format_ambiguous_delivery_outcome(
        "timeout",
        {"steps": ["нажатие «Предложить»", "ожидание"]},
    )
    assert "Мои отклики" in text
