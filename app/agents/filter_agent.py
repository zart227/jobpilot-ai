import json
import re

import structlog

from app.config import get_settings
from app.llm.provider import get_llm_provider
from app.schemas.agent_state import JobPilotState
from app.utils.formatting import format_budget

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are FilterAgent for JobPilot AI.
Determine if a freelance job posting is relevant for the developer profile.

Respond ONLY with valid JSON:
{"relevant": true/false, "reason": "brief explanation"}

Rules:
- Accept remote web/backend/fullstack/AI automation/bot/parsing/API projects that match developer skills
- For Kwork and RUB budgets: small fixed budgets (from 500 RUB) are OK for MVP tasks; do not reject only because budget looks low in rubles
- Accept AI agent, Telegram bot, FastAPI, Django, Laravel, React, Vue, parsing/monitoring projects when skills overlap
- Reject jobs that require excluded skills (see developer profile) as primary stack, e.g. n8n-only automation setup
- Reject: spam, clearly unrelated domains (design-only, SEO-only, copywriting-only), illegal content, or zero skill overlap
- When description is short but title clearly matches developer skills, lean toward relevant=true"""


class FilterAgent:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._llm = get_llm_provider()
        self._excluded = [
            s.strip().lower()
            for s in self._settings.developer_excluded_skills.split(",")
            if s.strip()
        ]

    def _matches_excluded_skill(self, job: dict) -> str | None:
        text = f"{job.get('title', '')} {job.get('description', '')}".lower()
        for skill in self._excluded:
            if skill in text:
                return skill
        return None

    async def run(self, state: JobPilotState) -> dict:
        job = state.get("job_data", {})
        excluded = self._matches_excluded_skill(job)
        if excluded:
            logger.info("FilterAgent rejected excluded skill", skill=excluded, title=job.get("title"))
            return {
                "is_relevant": False,
                "relevance_reason": f"Требуется {excluded}, которого нет в профиле разработчика",
            }

        skills = self._settings.developer_skills

        budget_label = format_budget(
            job.get("budget_min"),
            job.get("budget_max"),
            job.get("budget_currency", "USD"),
            job.get("platform"),
        )

        user_prompt = f"""Developer profile:
Name: {self._settings.developer_name}
Skills: {skills}
Excluded skills (do NOT accept as primary requirement): {self._settings.developer_excluded_skills}
Hourly rate: ${self._settings.developer_hourly_rate}
Bio: {self._settings.developer_bio}

Job posting:
Platform: {job.get('platform', 'unknown')}
Title: {job.get('title', '')}
Description: {job.get('description', '')[:2000]}
Budget: {budget_label}
Required skills: {', '.join(job.get('skills', []))}
Client: {job.get('client_name', 'Unknown')} (rating: {job.get('client_rating')})"""

        try:
            response = await self._llm.complete(SYSTEM_PROMPT, user_prompt, temperature=0.2)
            parsed = self._parse_response(response)
            logger.info(
                "FilterAgent decision",
                relevant=parsed["relevant"],
                title=job.get("title"),
            )
            return {
                "is_relevant": parsed["relevant"],
                "relevance_reason": parsed["reason"],
            }
        except Exception as exc:
            logger.error("FilterAgent failed", error=str(exc))
            return {
                "is_relevant": False,
                "relevance_reason": f"FilterAgent error: {exc}",
                "error": str(exc),
            }

    def _parse_response(self, response: str) -> dict:
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return {
                "relevant": bool(data.get("relevant", False)),
                "reason": str(data.get("reason", "No reason provided")),
            }
        return {"relevant": False, "reason": "Failed to parse FilterAgent response"}


async def filter_node(state: JobPilotState) -> dict:
    agent = FilterAgent()
    return await agent.run(state)
