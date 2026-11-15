import uuid

from app.services.send_attempt_service import (
    content_fingerprint,
    inbox_reply_idempotency_key,
    proposal_idempotency_key,
)
from app.utils.delivery_errors import is_ambiguous_send_error


def test_content_fingerprint_stable() -> None:
    assert content_fingerprint("hello") == content_fingerprint("hello")
    assert content_fingerprint("a") != content_fingerprint("b")


def test_proposal_idempotency_key_includes_version() -> None:
    pid = uuid.uuid4()
    k1 = proposal_idempotency_key(pid, 1, "text")
    k2 = proposal_idempotency_key(pid, 2, "text")
    assert k1 != k2


def test_inbox_reply_idempotency_key() -> None:
    pending = uuid.uuid4()
    assert inbox_reply_idempotency_key(pending, "hi") == inbox_reply_idempotency_key(pending, "hi")


def test_is_ambiguous_send_error() -> None:
    assert is_ambiguous_send_error("Connection timeout while sending") is True
    assert is_ambiguous_send_error("order closed") is False
    form_err = (
        "Не удалось заполнить форму: Locator.wait_for: Timeout 30000ms exceeded."
    )
    assert is_ambiguous_send_error(form_err) is False


def test_unknown_result_retryable_for_form_fill_label() -> None:
    from types import SimpleNamespace

    from app.services.send_attempt_service import unknown_result_is_retryable

    attempt = SimpleNamespace(
        status="unknown_result",
        last_error="form fill",
        platform_response={"error_label": "form_fill_error"},
    )
    assert unknown_result_is_retryable(attempt) is True

    live = SimpleNamespace(
        status="unknown_result",
        last_error="timeout",
        platform_response={"offer_action": "created"},
    )
    assert unknown_result_is_retryable(live) is False


def test_unknown_result_retryable_when_ambiguous_navigation_exception() -> None:
    from types import SimpleNamespace

    from app.services.send_attempt_service import unknown_result_is_retryable

    attempt = SimpleNamespace(
        status="unknown_result",
        last_error="Page.goto: chrome-error://chromewebdata/",
        platform_response={
            "ambiguous": True,
            "error_label": "exception",
        },
    )
    assert unknown_result_is_retryable(attempt) is True


def test_unknown_result_not_retryable_when_ambiguous_post_submit() -> None:
    from types import SimpleNamespace

    from app.services.send_attempt_service import unknown_result_is_retryable

    attempt = SimpleNamespace(
        status="unknown_result",
        last_error="timeout after submit",
        platform_response={"ambiguous": True, "error_label": "submit_timeout"},
    )
    assert unknown_result_is_retryable(attempt) is False
