
import structlog

from app.config import get_settings
from app.llm.errors import LLMServiceError
from app.llm.provider import get_simple_llm_provider
from app.schemas.agent_state import JobPilotState
from app.schemas.llm_outputs import is_validation_error, parse_filter_output
from app.services.opportunity_policy import client_rejection
from app.services.pipeline_llm_ledger import append_stage_log
from app.utils.formatting import format_budget

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """Determine if a freelance job posting is relevant for the developer profile.

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
        self._llm = get_simple_llm_provider()
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
        if self._settings.review_first_mode:
            reason = client_rejection(job, self._settings.client_min_rating, self._settings.client_min_paid_orders)
            if reason:
                return {"is_relevant": False, "relevance_reason": reason}
        if job.get("platform") == "kwork":
            from app.services.kwork_portfolio_filter import job_requires_kwork_portfolio

            requires_portfolio, portfolio_reason = job_requires_kwork_portfolio(job)
            if requires_portfolio:
                logger.info(
                    "FilterAgent rejected Kwork portfolio category",
                    title=job.get("title"),
                    reason=portfolio_reason,
                )
                return {
                    "is_relevant": False,
                    "relevance_reason": portfolio_reason,
                }

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

        strategy = ""
        if self._settings.review_first_mode:
            strategy = f"""
Goal: first reviews with scarce connects. Accept ONLY clearly bounded
single deliverables within {self._settings.review_max_hours} working hours.
Prefer one bug fix, small script, one API integration or small bot change.
Reject full products, multi-feature bots, vague scope, ongoing work or unknown effort.
Low payment is acceptable. Never infer small effort from low budget or 'simple'.
Return estimated_hours (number) and scope_clear (boolean) in the JSON as well.
"""
        try:
            response = await self._llm.complete(SYSTEM_PROMPT + strategy, user_prompt, temperature=0.2)
            parsed = parse_filter_output(response)
            relevant = parsed.relevant
            reason = parsed.reason
            if self._settings.review_first_mode:
                hours = parsed.estimated_hours
                if not parsed.scope_clear or hours is None or not 0 < hours <= self._settings.review_max_hours:
                    relevant = False
                    reason = "Объём неясен или задача превышает лимит часов"
            logger.info(
                "FilterAgent decision",
                relevant=relevant,
                title=job.get("title"),
            )
            return {
                "is_relevant": relevant,
                "relevance_reason": reason,
                "llm_stage_log": append_stage_log(state, "filter", "simple"),
            }
        except LLMServiceError as exc:
            logger.error("FilterAgent LLM error", error=str(exc), kind=exc.kind.value)
            return {
                "pipeline_processing_error": True,
                "processing_retryable": exc.retryable,
                "processing_stage": "filter",
                "error": str(exc),
                "relevance_reason": str(exc),
                "llm_stage_log": append_stage_log(state, "filter", "simple"),
            }
        except Exception as exc:
            logger.error("FilterAgent failed", error=str(exc))
            retryable = not is_validation_error(exc)
            return {
                "pipeline_processing_error": True,
                "processing_retryable": retryable,
                "processing_stage": "filter",
                "error": str(exc),
                "relevance_reason": f"FilterAgent error: {exc}",
                "llm_stage_log": append_stage_log(state, "filter", "simple"),
            }


async def filter_node(state: JobPilotState) -> dict:
    agent = FilterAgent()
    return await agent.run(state)
