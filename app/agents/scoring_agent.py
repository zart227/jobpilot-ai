import json
import re

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db.models import ScoringWeight
from app.db.session import AsyncSessionLocal
from app.llm.provider import get_llm_provider
from app.schemas.agent_state import JobPilotState

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are ScoringAgent for JobPilot AI.
Score the job opportunity from 0 to 100 based on weighted criteria.

Respond ONLY with valid JSON:
{
  "score": 75,
  "breakdown": {
    "skill_match": 80,
    "budget": 70,
    "complexity": 65,
    "client_quality": 75,
    "competition": 60
  },
  "summary": "brief scoring rationale"
}

Higher score = better opportunity for this developer.
If job requires excluded skills as primary stack, score below 20."""


class ScoringAgent:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._llm = get_llm_provider()

    async def run(self, state: JobPilotState) -> dict:
        job = state.get("job_data", {})
        weights = await self._get_active_weights()

        user_prompt = f"""Developer profile:
Skills: {self._settings.developer_skills}
Excluded skills: {self._settings.developer_excluded_skills}
Hourly rate: ${self._settings.developer_hourly_rate}

Scoring weights (must apply):
{json.dumps(weights, indent=2)}

Job posting:
Title: {job.get('title', '')}
Description: {job.get('description', '')[:2000]}
Budget: {job.get('budget_min')} - {job.get('budget_max')} {job.get('budget_currency', 'USD')}
Skills required: {', '.join(job.get('skills', []))}
Client: {job.get('client_name')} | Rating: {job.get('client_rating')} | Reviews: {job.get('client_reviews', 0)}
Platform: {job.get('platform')}"""

        try:
            response = await self._llm.complete(SYSTEM_PROMPT, user_prompt, temperature=0.3)
            parsed = self._parse_response(response)
            weighted_score = self._compute_weighted_score(parsed["breakdown"], weights)
            final_score = min(100, max(0, int(weighted_score)))

            logger.info("ScoringAgent result", score=final_score, title=job.get("title"))
            return {
                "score": final_score,
                "score_breakdown": parsed["breakdown"],
            }
        except Exception as exc:
            logger.error("ScoringAgent failed", error=str(exc))
            return {"score": 0, "score_breakdown": {}, "error": str(exc)}

    async def _get_active_weights(self) -> dict[str, float]:
        default = json.loads(self._settings.scoring_weights)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ScoringWeight)
                .where(ScoringWeight.is_active.is_(True))
                .order_by(ScoringWeight.created_at.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row:
                return row.weights
        return default

    def _compute_weighted_score(
        self, breakdown: dict[str, float], weights: dict[str, float]
    ) -> float:
        total = 0.0
        weight_sum = 0.0
        for key, weight in weights.items():
            value = breakdown.get(key, 50.0)
            total += float(value) * float(weight)
            weight_sum += float(weight)
        return total / weight_sum if weight_sum else 50.0

    def _parse_response(self, response: str) -> dict:
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if not match:
            return {"score": 50, "breakdown": {}, "summary": "parse error"}
        data = json.loads(match.group())
        return {
            "score": int(data.get("score", 50)),
            "breakdown": {k: float(v) for k, v in data.get("breakdown", {}).items()},
            "summary": str(data.get("summary", "")),
        }


async def scoring_node(state: JobPilotState) -> dict:
    agent = ScoringAgent()
    return await agent.run(state)
