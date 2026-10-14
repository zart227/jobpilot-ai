from app.services.notification_outbox import compute_backoff_seconds


def test_compute_backoff_seconds_grows_and_caps() -> None:
    assert compute_backoff_seconds(1, 30) == 30
    assert compute_backoff_seconds(2, 30) == 60
    assert compute_backoff_seconds(10, 30) == 3600
