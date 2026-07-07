"""Smoke-test Ollama simple LLM provider with real agent prompts."""

import asyncio
import json
import re
import sys

from app.agents.filter_agent import SYSTEM_PROMPT as FILTER_PROMPT
from app.agents.scoring_agent import SYSTEM_PROMPT as SCORING_PROMPT
from app.config import get_settings
from app.llm.provider import get_simple_llm_provider


def extract_json(response: str) -> dict | None:
    match = re.search(r"\{.*\}", response, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


async def run_case(name: str, system: str, user: str, temperature: float) -> None:
    settings = get_settings()
    provider = get_simple_llm_provider()
    print(f"\n{'=' * 60}")
    print(f"CASE: {name}")
    print(f"Provider: {type(provider).__name__}")
    print(f"Model: {settings.ollama_model} @ {settings.ollama_base_url}")
    print("-" * 60)

    try:
        raw = await provider.complete(system, user, temperature=temperature)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return

    print(f"Raw response ({len(raw)} chars):")
    print(raw)
    print("-" * 60)

    parsed = extract_json(raw)
    if parsed is None:
        print("JSON parse: FAILED")
        return

    print("JSON parse: OK")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))


async def main() -> None:
    get_settings.cache_clear()

    filter_user = """Developer profile:
Name: Test Dev
Skills: Python, FastAPI, Django, React, PostgreSQL, Docker
Excluded skills: n8n, Make.com
Hourly rate: $50
Bio: Full-stack developer

Job posting:
Platform: kwork
Title: Telegram-бот для уведомлений
Description: Нужен бот на Python с FastAPI и PostgreSQL. Интеграция с внешним API.
Budget: 5000 - 15000 RUB
Required skills: Python, Telegram
Client: Ivan (rating: 4.8)"""

    scoring_user = """Developer profile:
Skills: Python, FastAPI, Django, React, PostgreSQL, Docker
Excluded skills: n8n
Hourly rate: $50

Scoring weights (must apply):
{"skill_match": 0.3, "budget": 0.2, "complexity": 0.15, "client_quality": 0.2, "competition": 0.15}

Job posting:
Title: Telegram-бот для уведомлений
Description: Нужен бот на Python с FastAPI и PostgreSQL.
Budget: 5000 - 15000 RUB
Skills required: Python, Telegram
Client: Ivan | Rating: 4.8 | Reviews: 12
Platform: kwork"""

    reject_user = """Developer profile:
Name: Test Dev
Skills: Python, FastAPI
Excluded skills: n8n, Make.com
Hourly rate: $50
Bio: Backend developer

Job posting:
Platform: kwork
Title: Скрипт на n8n для автоматизации
Description: Настроить workflow в n8n, интеграции с CRM
Budget: 3000 RUB
Required skills: n8n
Client: Unknown (rating: None)"""

    await run_case("FilterAgent — relevant job", FILTER_PROMPT, filter_user, 0.2)
    await run_case("FilterAgent — excluded skill", FILTER_PROMPT, reject_user, 0.2)
    await run_case("ScoringAgent", SCORING_PROMPT, scoring_user, 0.3)


if __name__ == "__main__":
    asyncio.run(main())
