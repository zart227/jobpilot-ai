import pytest
from pydantic import ValidationError

from app.schemas.llm_outputs import parse_filter_output, parse_scoring_output


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
