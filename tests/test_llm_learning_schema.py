import json

from app.schemas.llm_outputs import parse_learning_output
from app.services.scoring_weights_validation import expected_weight_keys


def test_parse_learning_output_parses_structure() -> None:
    keys = expected_weight_keys()
    weights = {key: 0.2 for key in keys}
    raw = json.dumps(
        {"weights": weights, "insights": ["ok"], "top_platforms": ["kwork"]},
        ensure_ascii=False,
    )
    parsed = parse_learning_output(raw)
    assert set(parsed.weights.keys()) == set(keys)
    assert parsed.top_platforms == ["kwork"]
