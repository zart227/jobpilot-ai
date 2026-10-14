"""Per-job LLM stage log (no token billing; avoids double-counting retries in one pipeline run)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.config import Settings, get_settings


def _provider_label(settings: Settings, bucket: str) -> str:
    if (
        settings.llm_provider == "openrouter"
        and bucket in {"main", "scoring"}
        and (bucket != "scoring" or settings.llm_simple_provider != "ollama")
    ):
        return f"openrouter:{settings.openrouter_model}"
    if bucket == "scoring":
        if settings.llm_simple_provider == "ollama" or settings.llm_provider == "ollama":
            return f"ollama:{settings.ollama_scoring_model}"
        return f"{settings.llm_provider}:{settings.openai_model}"
    if bucket == "main":
        return f"{settings.llm_provider}:{settings.openai_model}"
    simple = settings.llm_simple_provider
    if simple == "same":
        simple = settings.llm_provider
    if simple == "openrouter":
        model = (settings.openrouter_model if settings.llm_simple_provider == "same"
                 else settings.openrouter_simple_model)
        return f"openrouter:{model}"
    if simple == "ollama":
        return f"ollama:{settings.ollama_model}"
    return f"{simple}:{settings.openai_simple_model}"


def stage_entry(stage: str, bucket: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "stage": stage,
        "bucket": bucket,
        "provider": _provider_label(settings, bucket),
        "at": datetime.now(UTC).isoformat(),
    }


def append_stage_log(state: dict[str, Any], stage: str, bucket: str) -> list[dict[str, Any]]:
    log = list(state.get("llm_stage_log") or [])
    log.append(stage_entry(stage, bucket))
    return log


def persist_stage_log(job, state: dict[str, Any]) -> None:
    log = list(state.get("llm_stage_log") or [])
    if not log:
        return
    raw = dict(job.raw_data or {})
    raw["llm_stage_log"] = log
    raw["llm_stage_count"] = len(log)
    by_stage: dict[str, int] = {}
    for entry in log:
        name = str(entry.get("stage", "unknown"))
        by_stage[name] = by_stage.get(name, 0) + 1
    raw["llm_stage_totals"] = by_stage
    job.raw_data = raw
