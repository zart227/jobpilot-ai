"""Untrusted model output must not become valid by coercion or discarded fields."""

import json

import pytest
from pydantic import ValidationError

from app.schemas.llm_outputs import parse_chat_output, parse_filter_output, parse_learning_output
from app.services.scoring_weights_validation import expected_weight_keys, validate_scoring_weights


@pytest.mark.parametrize("field,bad", [
    ("relevant", "true"), ("relevant", 1), ("scope_clear", "false"),
    ("scope_clear", 0), ("estimated_hours", "2"), ("estimated_hours", True),
    ("estimated_hours", -1), ("estimated_hours", float("nan")),
    ("estimated_hours", float("inf")), ("unexpected", "ignored"),
])
def test_filter_rejects_coercion_and_unknown_fields(field, bad):
    data = {"relevant": True, "reason": "ok", "scope_clear": True, "estimated_hours": 2}
    data[field] = bad
    with pytest.raises(ValidationError):
        parse_filter_output(json.dumps(data))


@pytest.mark.parametrize("bad", ["0.2", True, False, None, -0.1, float("nan"), float("inf")])
def test_learning_rejects_invalid_raw_weight(bad):
    weights = dict.fromkeys(expected_weight_keys(), 0.2)
    weights[expected_weight_keys()[0]] = bad
    with pytest.raises(ValidationError):
        parse_learning_output(json.dumps({"weights": weights}))


@pytest.mark.parametrize("mutation", ["missing", "extra", "all_zero", "overflow", "unexpected_field"])
def test_learning_rejects_invalid_structure(mutation):
    weights = dict.fromkeys(expected_weight_keys(), 0.2)
    data = {"weights": weights, "insights": [], "top_platforms": []}
    if mutation == "missing":
        del weights[expected_weight_keys()[0]]
    elif mutation == "extra":
        weights["other"] = 0.1
    elif mutation == "all_zero":
        data["weights"] = dict.fromkeys(weights, 0)
    elif mutation == "overflow":
        data["weights"] = dict.fromkeys(weights, 1e308)
    else:
        data["unexpected"] = 1
    with pytest.raises(ValidationError):
        parse_learning_output(json.dumps(data))


def test_chat_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        parse_chat_output('{"intent":"question","reply":"ok","unexpected":true}')


@pytest.mark.parametrize("data", [
    {"intent": "question", "reply": True}, {"intent": "unknown", "reply": "ok"},
    {"intent": "question"}, {"reply": "ok"},
])
def test_chat_rejects_invalid_or_missing_required_fields(data):
    with pytest.raises(ValidationError):
        parse_chat_output(json.dumps(data))


@pytest.mark.parametrize("field", ["insights", "top_platforms"])
def test_learning_rejects_non_string_list_items(field):
    data = {"weights": dict.fromkeys(expected_weight_keys(), 0.2), field: [1]}
    with pytest.raises(ValidationError):
        parse_learning_output(json.dumps(data))


def test_weight_validation_also_rejects_bool_for_non_llm_callers():
    weights = dict.fromkeys(expected_weight_keys(), 0.2)
    weights[expected_weight_keys()[0]] = True
    with pytest.raises(ValueError):
        validate_scoring_weights(weights)


def test_strict_schemas_accept_valid_payloads():
    assert parse_filter_output('{"relevant":true,"reason":"ok","estimated_hours":2}').estimated_hours == 2
    assert parse_chat_output('{"intent":"other","reply":"ok"}').reply == "ok"
    weights = dict.fromkeys(expected_weight_keys(), 0.2)
    assert parse_learning_output(json.dumps({"weights": weights})).weights == weights
