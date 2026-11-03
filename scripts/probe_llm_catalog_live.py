"""Live probe for OpenRouter/Ollama model catalogs (no chat completion, no secrets in output)."""

from __future__ import annotations

import asyncio
import json

from app.config import get_settings
from app.llm.model_catalog import (
    ModelTask,
    fetch_ollama_models,
    fetch_openrouter_models,
    is_auto_model_name,
    resolve_ollama_model,
    resolve_openrouter_model,
)


async def main() -> int:
    settings = get_settings()
    report: dict[str, object] = {}

    if settings.openrouter_api_key.strip():
        try:
            records = await fetch_openrouter_models(settings)
            from app.llm.model_catalog import (
                _openrouter_supports_chat,
                openrouter_model_is_confirmed_free,
            )

            free_ids = sorted(
                str(r.get("id"))
                for r in records
                if isinstance(r, dict)
                and r.get("id")
                and openrouter_model_is_confirmed_free(r.get("pricing"))
                and _openrouter_supports_chat(r, min_context=4096)
            )
            report["openrouter"] = {
                "catalog_count": len(records),
                "confirmed_free_chat_count": len(free_ids),
                "auto_simple_resolved": await resolve_openrouter_model(
                    settings, settings.openrouter_simple_model, task=ModelTask.SIMPLE
                )
                if is_auto_model_name(settings.openrouter_simple_model)
                else settings.openrouter_simple_model,
                "sample_free_ids": free_ids[:5],
            }
        except Exception as exc:
            report["openrouter"] = {"error": type(exc).__name__, "message": str(exc)[:200]}
    else:
        report["openrouter"] = {"skipped": "OPENROUTER_API_KEY not set"}

    try:
        tags = await fetch_ollama_models(settings)
        names = [str(m.get("name") or m.get("model") or "") for m in tags if isinstance(m, dict)]
        names = [n for n in names if n]
        report["ollama"] = {
            "installed_count": len(names),
            "sample_names": sorted(names)[:8],
            "auto_routine_resolved": await resolve_ollama_model(
                settings, settings.ollama_model, task=ModelTask.SIMPLE
            )
            if is_auto_model_name(settings.ollama_model)
            else settings.ollama_model,
        }
    except Exception as exc:
        report["ollama"] = {"error": type(exc).__name__, "message": str(exc)[:200]}

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
