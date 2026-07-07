import structlog

from app.config import get_settings
from app.llm.errors import classify_llm_error
from app.llm.provider import get_simple_llm_provider
from app.memory.qdrant_store import get_memory_store
from app.utils.formatting import (
    KWORK_MAX_OFFER_CHARS,
    competitive_offer_price,
    finalize_kwork_proposal,
    format_budget,
    format_offer_price,
    sanitize_proposal_text,
)

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You edit freelance proposal text based on the user's instruction.

Rules (critical):
- Apply ONLY what the user asked. Do not rewrite unrelated parts.
- Keep the same language as the original (Russian for Kwork).
- Plain text ONLY: no markdown, no asterisks, no bold/italic.
- Do NOT use em dash (—). Use commas, periods, or hyphen (-) instead.
- If YOUR OFFER PRICE is present in the original, keep that exact amount once.
- Never invent a new price. Never exceed the offer price.
- For Kwork: stay under 1200 characters when possible, hard max {max_chars}.
- Do NOT suggest calls or meetings unless already present.
- Return ONLY the revised proposal text, no labels or commentary."""

KWORK_EXTRA = """
Kwork offer rules:
- Sound like an experienced freelancer, not a template.
- Do not add filler ("готов взяться", "имею большой опыт").
- Keep numbered clarifying questions if the user did not ask to remove them."""


class EditProposalAgent:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._llm = get_simple_llm_provider()
        self._memory = get_memory_store()

    async def run(
        self,
        *,
        current_text: str,
        instruction: str,
        job: dict,
    ) -> str:
        is_kwork = job.get("platform") == "kwork"
        job_context = f"{job.get('title', '')} {job.get('description', '')[:400]}"
        preferences = await self._memory.search_edit_preferences(job_context, limit=3)

        pref_block = ""
        if preferences:
            pref_block = "\n\nUser's typical edit requests (honor when relevant):\n"
            for item in preferences:
                pref_block += f"- {item.get('instruction', '')}\n"

        budget_label = format_budget(
            job.get("budget_min"),
            job.get("budget_max"),
            job.get("budget_currency", "RUB"),
            job.get("platform"),
        )

        offer_line = ""
        if is_kwork:
            offer = format_offer_price(
                job.get("budget_min"),
                job.get("budget_max"),
                job.get("budget_currency", "RUB"),
                self._settings.kwork_offer_discount_percent,
            )
            if offer:
                offer_line = f"\nYOUR OFFER PRICE (keep exactly once if present): {offer}\n"

        system = SYSTEM_PROMPT.format(max_chars=KWORK_MAX_OFFER_CHARS)
        if is_kwork:
            system += KWORK_EXTRA

        user_prompt = f"""Job:
Platform: {job.get('platform', 'unknown')}
Title: {job.get('title', '')}
Budget: {budget_label}{offer_line}
{pref_block}

Current proposal:
{current_text}

User instruction:
{instruction}

Return the full revised proposal text."""

        try:
            response = await self._llm.complete(system, user_prompt, temperature=0.5)
            edited = sanitize_proposal_text(response.strip())
            if is_kwork:
                offer = competitive_offer_price(
                    job.get("budget_min"),
                    job.get("budget_max"),
                    self._settings.kwork_offer_discount_percent,
                )
                edited = finalize_kwork_proposal(
                    edited,
                    client_name=job.get("client_name"),
                    offer_price=offer,
                    currency=job.get("budget_currency", "RUB"),
                )
            logger.info(
                "EditProposalAgent revised proposal",
                platform=job.get("platform"),
                instruction_preview=instruction[:80],
                length=len(edited),
            )
            return edited
        except Exception as exc:
            logger.error("EditProposalAgent failed", error=str(exc))
            raise classify_llm_error(exc) from exc
