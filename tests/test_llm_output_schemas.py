import json

import pytest
from pydantic import ValidationError

from app.schemas.llm_outputs import parse_filter_output, parse_scoring_output

CRITERIA = ("skill_match", "budget", "complexity", "client_quality", "competition")


@pytest.mark.parametrize("criterion", CRITERIA)
@pytest.mark.parametrize("bad_value", ["50", True, False, None, float("nan"), float("inf"), float("-inf"), -1, 101])
def test_each_scoring_criterion_rejects_invalid_raw_values(criterion, bad_value):
    breakdown = dict.fromkeys(CRITERIA, 50)
    breakdown[criterion] = bad_value
    with pytest.raises((ValidationError, ValueError)):
        parse_scoring_output(json.dumps({"breakdown": breakdown}), CRITERIA)


@pytest.mark.parametrize("criterion", CRITERIA)
def test_each_scoring_criterion_is_required(criterion):
    breakdown = dict.fromkeys(CRITERIA, 50)
    del breakdown[criterion]
    with pytest.raises((ValidationError, ValueError)):
        parse_scoring_output(json.dumps({"breakdown": breakdown}), CRITERIA)


def test_scoring_accepts_integer_and_float_boundaries():
    breakdown = dict(zip(CRITERIA, (0, 100, 0.5, 99.5, 50)))
    result = parse_scoring_output(json.dumps({"breakdown": breakdown}), CRITERIA)
    assert result.breakdown.model_dump() == breakdown


def test_parse_filter_output() -> None:
    out = parse_filter_output('{"relevant": true, "reason": "ok", "scope_clear": true, "estimated_hours": 2}')
    assert out.relevant is True


def test_parse_scoring_rejects_missing_criterion() -> None:
    payload = (
        '{"breakdown": {"skill_match": 80, "budget": 70, "complexity": 60, '
        '"client_quality": 75}, "summary": "x"}'
    )
    with pytest.raises(ValueError, match="missing scoring criterion"):
        parse_scoring_output(payload, ("skill_match", "budget", "complexity", "client_quality", "competition"))


def test_parse_scoring_rejects_out_of_range() -> None:
    payload = (
        '{"breakdown": {"skill_match": 101, "budget": 70, "complexity": 60, '
        '"client_quality": 75, "competition": 50}, "summary": "x"}'
    )
    with pytest.raises(ValidationError):
        parse_scoring_output(
            payload,
            ("skill_match", "budget", "complexity", "client_quality", "competition"),
        )


def test_parse_scoring_rejects_nan() -> None:
    payload = (
        '{"breakdown": {"skill_match": NaN, "budget": 70, "complexity": 60, '
        '"client_quality": 75, "competition": 50}, "summary": "x"}'
    )
    with pytest.raises((ValidationError, ValueError)):
        parse_scoring_output(
            payload,
            ("skill_match", "budget", "complexity", "client_quality", "competition"),
        )
