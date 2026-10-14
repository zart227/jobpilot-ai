import json
from collections import Counter

import structlog
from sqlalchemy import func, select, update

from app.config import get_settings
from app.db.models import Outcome, Proposal, Reward, ScoringWeight
from app.db.session import AsyncSessionLocal
from app.llm.provider import get_simple_llm_provider
from app.memory.qdrant_store import get_memory_store
from app.schemas.agent_state import JobPilotState
from app.schemas.llm_outputs import is_validation_error, parse_learning_output
from app.services.reward_constants import REWARD_MAP
from app.services.scoring_weights_validation import validate_scoring_weights

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """Analyze proposal outcomes and suggest improvements.

Based on historical data, provide:
1. Adjusted scoring weights (must sum to 1.0)
2. Key insights for future proposals
3. Job types/platforms ranked by success

Respond ONLY with valid JSON:
{
  "weights": {"skill_match": 0.30, "budget": 0.20, "complexity": 0.15, "client_quality": 0.20, "competition": 0.15},
  "insights": ["insight1", "insight2"],
  "top_platforms": ["platform1", "platform2"]
}"""


class LearningAgent:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._llm = get_simple_llm_provider()
        self._memory = get_memory_store()

    async def run(self, state: JobPilotState) -> dict:
        outcome_status = state.get("outcome_status", "sent")
        reward = REWARD_MAP.get(outcome_status, 0)

        stats = await self._gather_stats()
        user_prompt = f"""Recent outcome: {outcome_status} (reward: {reward})

Historical statistics:
{json.dumps(stats, indent=2, default=str)}

Current weights:
{self._settings.scoring_weights}

Analyze patterns and suggest weight adjustments."""

        try:
            response = await self._llm.complete(SYSTEM_PROMPT, user_prompt, temperature=0.3)
            parsed = parse_learning_output(response)
            await self._persist_weights(parsed.weights)
            await self._store_success_patterns(state, outcome_status)

            payload = {
                "weights": parsed.weights,
                "insights": parsed.insights,
                "top_platforms": parsed.top_platforms,
            }
            logger.info("LearningAgent updated weights", weights=parsed.weights)
            return {
                "reward": reward,
                "learning_notes": json.dumps(payload, ensure_ascii=False),
            }
        except Exception as exc:
            logger.error("LearningAgent failed", error=str(exc))
            return {
                "reward": reward,
                "learning_notes": str(exc),
                "error": str(exc),
                "pipeline_processing_error": is_validation_error(exc),
            }

    async def _gather_stats(self) -> dict:
        async with AsyncSessionLocal() as session:
            outcome_counts = await session.execute(
                select(Outcome.status, func.count()).group_by(Outcome.status)
            )
            reward_total = await session.execute(select(func.sum(Reward.reward_value)))
            platform_stats = await session.execute(
                select(
                    Proposal.job_id,
                    Outcome.status,
                ).join(Outcome, Outcome.proposal_id == Proposal.id)
            )

        counts = dict(outcome_counts.all())
        platforms: Counter[str] = Counter()
        for row in platform_stats.all():
            platforms[str(row[1])] += 1

        return {
            "outcome_counts": counts,
            "total_rewards": reward_total.scalar() or 0,
            "outcome_distribution": dict(platforms),
        }

    async def _outcome_count(self) -> int:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(func.count()).select_from(Outcome))
            return int(result.scalar() or 0)

    async def _persist_weights(self, weights: dict[str, float]) -> None:
        if self._settings.review_first_mode:
            logger.info("LearningAgent skipped weight activation (review_first_mode)")
            return
        if await self._outcome_count() < self._settings.learning_min_outcomes:
            logger.info(
                "LearningAgent skipped weight activation (insufficient outcomes)",
                min_required=self._settings.learning_min_outcomes,
            )
            return
        try:
            normalized = validate_scoring_weights(weights)
        except ValueError as exc:
            logger.warning("LearningAgent rejected invalid weights", error=str(exc))
            return

        async with AsyncSessionLocal() as session:
            await session.execute(update(ScoringWeight).values(is_active=False))
            session.add(
                ScoringWeight(weights=normalized, source="learning_agent", is_active=True)
            )
            await session.commit()

    async def _store_success_patterns(self, state: JobPilotState, outcome: str) -> None:
        if outcome not in ("replied", "hired"):
            return
        proposal = state.get("edited_proposal") or state.get("proposal_content", "")
        proposal_id = str(state.get("proposal_id", "") or "")
        version = state.get("proposal_version", "")
        job = state.get("job_data", {})
        if proposal and proposal_id:
            memory_key = f"{proposal_id}:{outcome}:{version}"
            await self._memory.store_successful_proposal(
                memory_key,
                proposal,
                {
                    "outcome": outcome,
                    "platform": job.get("platform"),
                    "proposal_id": proposal_id,
                    "proposal_version": version,
                },
            )
            for sentence in proposal.split(".")[:3]:
                phrase = sentence.strip()
                if len(phrase) > 20:
                    await self._memory.store_pricing_phrase(phrase, 0.8 if outcome == "hired" else 0.5)

async def learning_node(state: JobPilotState) -> dict:
    agent = LearningAgent()
    return await agent.run(state)
