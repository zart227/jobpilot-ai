from app.services.ops_stats_format import format_ops_stats_telegram


def test_stats_telegram_lists_unknown_previews() -> None:
    text = format_ops_stats_telegram(
        {
            "send_attempts": {
                "unknown_result": 1,
                "known_failed": 2,
                "sending": 0,
                "unknown_result_previews": [
                    {
                        "order_id": "3271980",
                        "title": "Собрать фото",
                        "last_error": "Page.goto: chrome-error://chromewebdata/",
                        "error_label": "exception",
                    }
                ],
            },
            "notification_outbox": {"pending_or_claimed": 0, "needs_attention": 0},
            "jobs": {"retryable_error": 0, "permanent_error": 0},
            "last_success": {},
        }
    )
    assert "3271980" in text
    assert "unknown_result=1" in text
    assert "chrome-error" not in text or "браузер" in text.lower() or "Kwork" in text
