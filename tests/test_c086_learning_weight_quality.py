"""C086: learned scoring weights require sample size and quality gates."""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.services.learning_weight_activation import (
    LearningActivationStats,
    evaluate_weight_activation_quality,
    should_activate_learned_weights,
)
from app.services.scoring_weights_validation import validate_scoring_weights


def _settings(**overrides) -> Settings:
    base = {
        "learning_min_outcomes": 5,
        "learning_min_positive_outcomes": 2,
        "learning_min_hired_outcomes": 1,
        "learning_max_weight_delta": 0.15,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_evaluate_rejects_insufficient_hired() -> None:
    reference = validate_scoring_weights(json.loads(Settings(_env_file=None).scoring_weights))
    proposed = dict(reference)
    proposed["skill_match"] = round(proposed["skill_match"] + 0.05, 4)
    stats = LearningActivationStats(
        total_outcomes=10,
        outcome_counts={"replied": 5, "sent": 5},
    )
    ok, reason = evaluate_weight_activation_quality(proposed, reference, stats, _settings())
    assert ok is False
    assert reason == "insufficient_hired_outcomes"


def test_evaluate_rejects_large_delta() -> None:
    reference = validate_scoring_weights(json.loads(Settings(_env_file=None).scoring_weights))
    proposed = dict(reference)
    proposed["skill_match"] = round(reference["skill_match"] + 0.16, 4)
    proposed["competition"] = round(reference["competition"] - 0.16, 4)
    stats = LearningActivationStats(
        total_outcomes=12,
        outcome_counts={"replied": 4, "hired": 2},
    )
    ok, reason = evaluate_weight_activation_quality(proposed, reference, stats, _settings())
    assert ok is False
    assert reason.startswith("weight_delta_exceeds_limit:")


def test_evaluate_accepts_moderate_shift_with_enough_signal() -> None:
    reference = validate_scoring_weights(json.loads(Settings(_env_file=None).scoring_weights))
    proposed = validate_scoring_weights(
        {
            **reference,
            "skill_match": reference["skill_match"] + 0.05,
            "budget": reference["budget"] - 0.05,
        }
    )
    stats = LearningActivationStats(
        total_outcomes=12,
        outcome_counts={"replied": 4, "hired": 2, "sent": 6},
    )
    ok, reason = evaluate_weight_activation_quality(proposed, reference, stats, _settings())
    assert ok is True
    assert reason == "ok"


@pytest.mark.asyncio
async def test_should_activate_uses_active_row_as_reference():
    import os
    import uuid

    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        pytest.skip("integration DB required")

    from app.db.models import Job, Outcome
    from app.db.session import AsyncSessionLocal
    from app.services.scoring_weights_service import replace_active_scoring_weights

    reference = {
        "skill_match": 0.40,
        "budget": 0.10,
        "complexity": 0.10,
        "client_quality": 0.20,
        "competition": 0.20,
    }
    async with AsyncSessionLocal() as session:
        await replace_active_scoring_weights(session, reference)
        job_id = uuid.uuid4()
        session.add(
            Job(
                id=job_id,
                platform="kwork",
                external_id=f"c086-{job_id.hex[:8]}",
                title="c086",
                description="d",
                status="sent",
            )
        )
        for status, count in (("replied", 4), ("hired", 2), ("sent", 4)):
            for _ in range(count):
                session.add(
                    Outcome(
                        id=uuid.uuid4(),
                        job_id=job_id,
                        status=status,
                        reward=1,
                        event_key=f"c086-{uuid.uuid4()}",
                    )
                )
        await session.commit()

    proposed = dict(reference)
    proposed["skill_match"] = 0.45
    proposed["budget"] = 0.05

    async with AsyncSessionLocal() as session:
        ok, reason, normalized = await should_activate_learned_weights(
            session,
            proposed,
            _settings(learning_min_outcomes=8),
        )
    assert normalized is not None
    assert ok is True
    assert reason == "ok"
