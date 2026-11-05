"""Quality gates before activating learning-produced scoring weights."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Outcome, ScoringWeight
from app.services.scoring_weights_validation import expected_weight_keys, validate_scoring_weights


@dataclass(frozen=True)
class LearningActivationStats:
    total_outcomes: int
    outcome_counts: dict[str, int]

    @property
    def positive_outcomes(self) -> int:
        return int(self.outcome_counts.get("replied", 0)) + int(
            self.outcome_counts.get("hired", 0)
        )

    @property
    def hired_outcomes(self) -> int:
        return int(self.outcome_counts.get("hired", 0))


async def gather_learning_activation_stats(session: AsyncSession) -> LearningActivationStats:
    result = await session.execute(select(Outcome.status, func.count()).group_by(Outcome.status))
    counts = {str(status): int(count) for status, count in result.all()}
    total = sum(counts.values())
    return LearningActivationStats(total_outcomes=total, outcome_counts=counts)


async def reference_scoring_weights(session: AsyncSession, settings: Settings) -> dict[str, float]:
    row = (
        await session.execute(
            select(ScoringWeight)
            .where(ScoringWeight.is_active.is_(True))
            .order_by(ScoringWeight.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row and row.weights:
        return validate_scoring_weights(row.weights)
    return validate_scoring_weights(json.loads(settings.scoring_weights))


def evaluate_weight_activation_quality(
    proposed: dict[str, float],
    reference: dict[str, float],
    stats: LearningActivationStats,
    settings: Settings,
) -> tuple[bool, str]:
    if stats.total_outcomes < settings.learning_min_outcomes:
        return False, "insufficient_total_outcomes"
    if stats.positive_outcomes < settings.learning_min_positive_outcomes:
        return False, "insufficient_positive_outcomes"
    if stats.hired_outcomes < settings.learning_min_hired_outcomes:
        return False, "insufficient_hired_outcomes"

    max_delta = settings.learning_max_weight_delta
    for key in expected_weight_keys():
        delta = abs(proposed[key] - reference[key])
        if delta > max_delta + 1e-9:
            return False, f"weight_delta_exceeds_limit:{key}"

    return True, "ok"


async def should_activate_learned_weights(
    session: AsyncSession,
    proposed: dict[str, Any],
    settings: Settings,
) -> tuple[bool, str, dict[str, float] | None]:
    try:
        normalized = validate_scoring_weights(proposed)
    except ValueError as exc:
        return False, f"invalid_weights:{exc}", None

    stats = await gather_learning_activation_stats(session)
    reference = await reference_scoring_weights(session, settings)
    ok, reason = evaluate_weight_activation_quality(normalized, reference, stats, settings)
    if not ok:
        return False, reason, normalized
    return True, reason, normalized
