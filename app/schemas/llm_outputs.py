"""Strict validation for LLM JSON outputs."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.services.scoring_weights_validation import validate_scoring_weights


class FilterLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevant: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=2000)
    estimated_hours: float | None = Field(default=None, strict=True)
    estimated_calendar_days: float | None = Field(default=None, strict=True)
    scope_clear: bool | None = Field(default=None, strict=True)
    minimum_price_rub: int | None = Field(default=None, strict=True, ge=500)
    complexity: Literal["simple", "moderate", "complex"] | None = None

    @field_validator("estimated_hours", "estimated_calendar_days")
    @classmethod
    def finite_hours(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not math.isfinite(value) or value < 0:
            raise ValueError("estimated_hours must be finite and non-negative")
        return value


class ScoringBreakdown(BaseModel):
    skill_match: float
    budget: float
    complexity: float
    client_quality: float
    competition: float

    @field_validator(
        "skill_match",
        "budget",
        "complexity",
        "client_quality",
        "competition",
        mode="before",
    )
    @classmethod
    def criterion_range(cls, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            # Pydantic turns ValueError into a ValidationError; TypeError escapes.
            raise ValueError("criterion must be numeric")  # noqa: TRY004
        if not math.isfinite(float(value)):
            raise ValueError("criterion must be finite")
        numeric = float(value)
        if numeric < 0 or numeric > 100:
            raise ValueError("criterion must be between 0 and 100")
        return numeric


class ScoringLLMOutput(BaseModel):
    breakdown: ScoringBreakdown
    summary: str = Field(default="", max_length=4000)
    score: int | None = Field(default=None, ge=0, le=100)


def _extract_json_blob(response: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", response, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in LLM response")
    return json.loads(match.group())


def parse_filter_output(response: str) -> FilterLLMOutput:
    data = _extract_json_blob(response)
    return FilterLLMOutput.model_validate(data)


def parse_scoring_output(response: str, required_keys: tuple[str, ...]) -> ScoringLLMOutput:
    data = _extract_json_blob(response)
    breakdown = data.get("breakdown") or {}
    if not isinstance(breakdown, dict):
        raise TypeError("breakdown must be an object")
    for key in required_keys:
        if key not in breakdown:
            raise ValueError(f"missing scoring criterion: {key}")
    return ScoringLLMOutput.model_validate(data)


CHAT_INTENTS = (
    "question",
    "negotiation",
    "acceptance",
    "rejection",
    "clarification",
    "other",
)


class ChatLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "question",
        "negotiation",
        "acceptance",
        "rejection",
        "clarification",
        "other",
    ]
    reply: str = Field(min_length=1, max_length=4000)


def parse_chat_output(response: str) -> ChatLLMOutput:
    data = _extract_json_blob(response)
    return ChatLLMOutput.model_validate(data)


class LearningLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weights: dict[str, float]
    insights: list[str] = Field(default_factory=list)
    top_platforms: list[str] = Field(default_factory=list)

    @field_validator("weights", mode="before")
    @classmethod
    def valid_raw_weights(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("weights must be an object")  # noqa: TRY004
        validate_scoring_weights(value)
        return value

    @field_validator("insights", "top_platforms")
    @classmethod
    def trim_strings(cls, value: list[str]) -> list[str]:
        return [str(item).strip()[:500] for item in value if str(item).strip()]


def parse_learning_output(response: str) -> LearningLLMOutput:
    data = _extract_json_blob(response)
    return LearningLLMOutput.model_validate(data)


def is_validation_error(exc: Exception) -> bool:
    return isinstance(exc, (ValidationError, ValueError))
