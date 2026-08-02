from app.llm.openai_usage import (
    OpenAIUsageSnapshot,
    parse_credit_grants,
    parse_pending_usage,
    sum_cost_buckets,
)

SAMPLE_CREDITS = {
    "object": "credit_summary",
    "total_granted": 23.0,
    "total_used": 3.07,
    "total_available": 4.9,
    "grants": {"object": "list", "data": []},
}

SAMPLE_COSTS = {
    "object": "page",
    "data": [
        {
            "object": "bucket",
            "results": [
                {"amount": {"value": 0.12, "currency": "usd"}},
                {"amount": {"value": 0.08, "currency": "usd"}},
            ],
        },
        {
            "object": "bucket",
            "results": [{"amount": {"value": 1.5, "currency": "usd"}}],
        },
    ],
}


def test_parse_credit_grants() -> None:
    granted, used, available = parse_credit_grants(SAMPLE_CREDITS)
    assert granted == 23.0
    assert used == 3.07
    assert available == 4.9


def test_parse_pending_usage_amount_object() -> None:
    assert parse_pending_usage({"amount": {"value": 0.42}}) == 0.42


def test_sum_cost_buckets() -> None:
    assert sum_cost_buckets(SAMPLE_COSTS) == 1.7


def test_snapshot_warning_and_critical() -> None:
    snapshot = OpenAIUsageSnapshot(credits_remaining_usd=4.0)
    assert snapshot.is_warning(5.0, 2.0)
    assert not snapshot.is_critical(2.0)

    low = OpenAIUsageSnapshot(credits_remaining_usd=1.5)
    assert low.is_critical(2.0)
    assert not low.is_warning(5.0, 2.0)
