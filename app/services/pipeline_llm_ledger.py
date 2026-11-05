"""Per-job LLM stage log with resolved model and token/cost usage (C115)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import Settings, get_settings
from app.llm.call_usage import LLMCallUsage
from app.llm.model_catalog import is_auto_model_name


def _format_model(settings: Settings, model: str) -> str:
    if is_auto_model_name(model):
        return f"{model}(catalog)"
    return model


def _provider_label(settings: Settings, bucket: str) -> str:
    if (
        settings.llm_provider == "openrouter"
        and bucket in {"main", "scoring"}
        and (bucket != "scoring" or settings.llm_simple_provider != "ollama")
    ):
        return f"openrouter:{_format_model(settings, settings.openrouter_model)}"
    if bucket == "scoring":
        if settings.llm_simple_provider == "ollama" or settings.llm_provider == "ollama":
            return f"ollama:{_format_model(settings, settings.ollama_scoring_model)}"
        return f"{settings.llm_provider}:{settings.openai_model}"
    if bucket == "main":
        return f"{settings.llm_provider}:{settings.openai_model}"
    simple = settings.llm_simple_provider
    if simple == "same":
        simple = settings.llm_provider
    if simple == "openrouter":
        model = (
            settings.openrouter_model
            if settings.llm_simple_provider == "same"
            else settings.openrouter_simple_model
        )
        return f"openrouter:{_format_model(settings, model)}"
    if simple == "ollama":
        return f"ollama:{_format_model(settings, settings.ollama_model)}"
    return f"{simple}:{settings.openai_simple_model}"


def stage_entry(
    stage: str,
    bucket: str,
    settings: Settings | None = None,
    *,
    usage: LLMCallUsage | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    entry: dict[str, Any] = {
        "stage": stage,
        "bucket": bucket,
        "provider": _provider_label(settings, bucket),
        "at": datetime.now(UTC).isoformat(),
    }
    if usage is not None:
        entry.update(usage.as_ledger_fields())
    return entry


def _upsert_stage_row(log: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    stage = entry.get("stage")
    bucket = entry.get("bucket")
    for index in range(len(log) - 1, -1, -1):
        row = log[index]
        if row.get("stage") == stage and row.get("bucket") == bucket:
            log[index] = entry
            return log
    log.append(entry)
    return log


def append_stage_log(
    state: dict[str, Any],
    stage: str,
    bucket: str,
    *,
    usage: LLMCallUsage | None = None,
) -> list[dict[str, Any]]:
    log = list(state.get("llm_stage_log") or [])
    entry = stage_entry(stage, bucket, usage=usage)
    return _upsert_stage_row(log, entry)


def summarize_usage_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    total_cost = 0.0
    total_prompt = 0
    total_completion = 0
    by_stage: dict[str, dict[str, Any]] = {}
    for entry in entries:
        stage = str(entry.get("stage", "unknown"))
        bucket = by_stage.setdefault(
            stage,
            {
                "calls": 0,
                "cost_usd": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
        )
        bucket["calls"] += 1
        cost = float(entry.get("cost_usd") or 0)
        prompt = int(entry.get("prompt_tokens") or 0)
        completion = int(entry.get("completion_tokens") or 0)
        bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 8)
        bucket["prompt_tokens"] += prompt
        bucket["completion_tokens"] += completion
        total_cost += cost
        total_prompt += prompt
        total_completion += completion
    return {
        "total_cost_usd": round(total_cost, 8),
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "by_stage": by_stage,
    }


def persist_stage_log(job, state: dict[str, Any]) -> None:
    log = list(state.get("llm_stage_log") or [])
    if not log:
        return
    attempt = job.processing_attempt_count
    attempt_id = (job.raw_data or {}).get("current_processing_attempt_id")
    enriched = []
    for entry in log:
        row = dict(entry)
        if attempt is not None:
            row["processing_attempt"] = attempt
        if attempt_id:
            row["processing_attempt_id"] = attempt_id
        enriched.append(row)
    raw = dict(job.raw_data or {})
    raw["llm_stage_log"] = enriched
    raw["llm_stage_count"] = len(enriched)
    by_stage: dict[str, int] = {}
    for entry in enriched:
        name = str(entry.get("stage", "unknown"))
        by_stage[name] = by_stage.get(name, 0) + 1
    raw["llm_stage_totals"] = by_stage
    raw["llm_usage_summary"] = summarize_usage_entries(enriched)
    job.raw_data = raw
