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
