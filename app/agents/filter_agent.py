
import uuid

import structlog

from app.config import get_settings
from app.llm.errors import LLMServiceError
from app.llm.provider import get_simple_llm_provider
from app.schemas.agent_state import JobPilotState
from app.schemas.llm_outputs import parse_filter_output
from app.services.opportunity_policy import client_rejection, kwork_opportunity_fingerprint
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
        quick_review = self._settings.review_first_mode or job.get("platform") == "kwork"
        if quick_review:
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

        description = job.get("description") or ""
        if job.get("platform") == "kwork" and (
            not description.strip() or description.strip() == (job.get("title") or "").strip()
        ):
            return {
                "is_relevant": False,
                "relevance_reason": "Полное описание задачи не получено",
            }
        if job.get("platform") == "kwork" and len(description) > 12000:
            return {
                "is_relevant": False,
                "relevance_reason": "Описание слишком длинное для полной оценки объёма",
            }

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
Description: {description if job.get('platform') == 'kwork' else description[:2000]}
Budget: {budget_label}
Required skills: {', '.join(job.get('skills', []))}
Deadline: {job.get('deadline') or 'Not specified'}
Client: {job.get('client_name', 'Unknown')} (rating: {job.get('client_rating')})"""

        strategy = ""
        if quick_review:
            strategy = f"""
Goal: first reviews with scarce connects. Accept ONLY simple clearly bounded
single deliverables within {min(self._settings.review_max_hours, 3)} working hours and one calendar day.
Prefer one bug fix, small script, one API integration or small bot change.
Reject full products, multi-feature bots, vague scope, ongoing work or unknown effort.
Low payment is acceptable. Never infer small effort from low budget or 'simple'.
Return estimated_hours (number), estimated_calendar_days (number, including dependencies
and waiting time), scope_clear (boolean), complexity (simple, moderate or complex).
If calendar duration is uncertain, set estimated_calendar_days to null.
For Kwork also return minimum_price_rub (integer, at least 500): the minimum reasonable
price for this specific scope, not simply the buyer's budget. Seller has no completed
Kwork orders yet: prefer the lower competitive end (at or below buyer desired budget_min),
not an inflated quote. Do not invent a clear scope.
For Kwork use exactly these JSON keys: relevant, reason, estimated_hours,
estimated_calendar_days, scope_clear, complexity, minimum_price_rub.
"""
        try:
            response = await self._llm.complete(SYSTEM_PROMPT + strategy, user_prompt, temperature=0.2)
            parsed = parse_filter_output(response)
            relevant = parsed.relevant
            reason = parsed.reason
            needs_kwork_assessment = job.get("platform") == "kwork"
            if quick_review:
                hours = parsed.estimated_hours
                if (not parsed.scope_clear or hours is None
                    or not 0 < hours <= min(self._settings.review_max_hours, 3)
                    or needs_kwork_assessment and (
                        parsed.complexity != "simple" or parsed.minimum_price_rub is None
                        or parsed.estimated_calendar_days is None
                        or not 0 < parsed.estimated_calendar_days <= 1
                    )):
                    relevant = False
                    reason = "Не подтверждены простой объём, цена, срок до 1 дня и трудозатраты до 3 часов"
                if needs_kwork_assessment and parsed.minimum_price_rub is not None:
                    budget_cap = job.get("budget_max")
                    if budget_cap is not None and parsed.minimum_price_rub > budget_cap:
                        relevant = False
                        reason = "Минимальная оценка ИИ превышает допустимый бюджет заказчика"
            logger.info(
                "FilterAgent decision",
                relevant=relevant,
                title=job.get("title"),
            )
            return {
                "is_relevant": relevant,
                "relevance_reason": reason,
                "opportunity_assessment": {
                    "relevant": relevant,
                    "assessment_id": str(uuid.uuid4()) if needs_kwork_assessment else None,
                    "source_fingerprint": kwork_opportunity_fingerprint(job) if needs_kwork_assessment else None,
                    "estimated_hours": parsed.estimated_hours,
                    "estimated_calendar_days": parsed.estimated_calendar_days,
                    "scope_clear": parsed.scope_clear,
                    "complexity": parsed.complexity,
                    "minimum_price_rub": parsed.minimum_price_rub,
                    "reason": reason,
                },
                "llm_stage_log": append_stage_log(
                    state,
                    "filter",
                    "simple",
                    usage=self._llm.consume_last_call_usage(),
                ),
            }
        except LLMServiceError as exc:
            logger.error("FilterAgent LLM error", error=str(exc), kind=exc.kind.value)
            from app.services.pipeline_processing_errors import pipeline_error_from_exception

            return {
                **pipeline_error_from_exception(exc, stage="filter"),
                "relevance_reason": str(exc),
            }
        except Exception as exc:
            logger.error("FilterAgent failed", error=str(exc))
            from app.services.pipeline_processing_errors import pipeline_error_from_exception

            return {
                **pipeline_error_from_exception(exc, stage="filter"),
                "relevance_reason": f"FilterAgent error: {exc}",
            }


async def filter_node(state: JobPilotState) -> dict:
    agent = FilterAgent()
    return await agent.run(state)
