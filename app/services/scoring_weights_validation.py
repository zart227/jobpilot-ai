"""Validate learning-produced scoring weights."""

from __future__ import annotations

import math
from typing import Any

from app.services.opportunity_policy import REVIEW_WEIGHTS


def expected_weight_keys() -> tuple[str, ...]:
    return tuple(REVIEW_WEIGHTS.keys())


def validate_scoring_weights(raw: dict[str, Any]) -> dict[str, float]:
    expected = set(expected_weight_keys())
    keys = set(raw.keys())
    if keys != expected:
        missing = expected - keys
        extra = keys - expected
        raise ValueError(f"invalid weight keys: missing={sorted(missing)} extra={sorted(extra)}")

    normalized: dict[str, float] = {}
    for key in expected_weight_keys():
        value = raw[key]
        if not isinstance(value, (int, float)):
            raise TypeError(f"weight {key} must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"weight {key} must be finite")
        if number < 0:
            raise ValueError(f"weight {key} must be nonnegative")
        normalized[key] = number

    total = sum(normalized.values())
    if total <= 0:
        raise ValueError("weights must sum to a positive value")

    return {key: round(normalized[key] / total, 4) for key in expected_weight_keys()}
