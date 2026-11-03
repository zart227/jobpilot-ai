"""Outbox inventory error buckets and operator hints (no Telegram sends)."""

import runpy
from pathlib import Path

import pytest

_module_path = Path(__file__).resolve().parents[1] / "scripts" / "ops_notification_outbox_inventory.py"
_mod = runpy.run_path(str(_module_path))
error_bucket = _mod["error_bucket"]
OPERATOR_HINTS_BY_KIND = _mod["OPERATOR_HINTS_BY_KIND"]


@pytest.mark.parametrize(
    ("last_error", "expected"),
    [
        ("Telegram timeout after part 2", "timeout"),
        ("Request timed out", "timeout"),
        ("claim lease_expired", "lease_expired"),
        ("partial delivery: message 3 failed", "partial_delivery"),
        (None, "unknown"),
        ("", "unknown"),
        ("something else", "other"),
    ],
)
def test_error_bucket_classification(last_error, expected):
    assert error_bucket(last_error) == expected


def test_operator_hints_cover_legacy_needs_attention_kinds():
    for kind in ("proposal_approval", "inbox_alert", "ops_alert"):
        assert kind in OPERATOR_HINTS_BY_KIND
        assert len(OPERATOR_HINTS_BY_KIND[kind]) > 20


def test_build_triage_groups_aggregates_without_payload():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    build = _mod["_build_triage_groups"]
    rows = [
        SimpleNamespace(
            kind="inbox_alert",
            last_error="timeout",
            id="a",
            reference_type="x",
            reference_id=None,
            telegram_message_id=1,
            created_at=datetime.now(UTC),
        ),
        SimpleNamespace(
            kind="inbox_alert",
            last_error="timed out again",
            id="b",
            reference_type="x",
            reference_id=None,
            telegram_message_id=2,
            created_at=datetime.now(UTC),
        ),
        SimpleNamespace(
            kind="proposal_approval",
            last_error="lease_expired",
            id="c",
            reference_type="y",
            reference_id="ref",
            telegram_message_id=None,
            created_at=datetime.now(UTC),
        ),
    ]
    groups = build(rows)
    assert len(groups) == 2
    inbox = next(g for g in groups if g["kind"] == "inbox_alert")
    assert inbox["error_bucket"] == "timeout"
    assert inbox["count"] == 2
    assert len(inbox["items"]) == 2
    assert "payload" not in str(inbox)


def test_count_by_reference_type():
    from types import SimpleNamespace

    count = _mod["_count_by_reference_type"]
    rows = [
        SimpleNamespace(reference_type="telegram_pending"),
        SimpleNamespace(reference_type="telegram_pending"),
        SimpleNamespace(reference_type="telegram_inbox_pending"),
        SimpleNamespace(reference_type=None),
    ]
    assert count(rows) == {"telegram_pending": 2, "telegram_inbox_pending": 1, "unknown": 1}
