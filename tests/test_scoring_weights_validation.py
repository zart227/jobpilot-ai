import math

import pytest

from app.services.scoring_weights_validation import validate_scoring_weights


def test_validate_scoring_weights_normalizes_sum() -> None:
    weights = validate_scoring_weights(
        {
            "skill_match": 1,
            "budget": 1,
            "complexity": 1,
            "client_quality": 1,
            "competition": 1,
        }
    )
    assert math.isclose(sum(weights.values()), 1.0, rel_tol=1e-6)


def test_validate_scoring_weights_rejects_extra_key() -> None:
    base = {
        "skill_match": 0.2,
        "budget": 0.2,
        "complexity": 0.2,
        "client_quality": 0.2,
        "competition": 0.2,
    }
    with pytest.raises(ValueError):
        validate_scoring_weights({**base, "unknown": 0.1})


def test_validate_scoring_weights_rejects_negative() -> None:
    with pytest.raises(ValueError):
        validate_scoring_weights(
            {
                "skill_match": -0.1,
                "budget": 0.3,
                "complexity": 0.2,
                "client_quality": 0.3,
                "competition": 0.3,
            }
        )
