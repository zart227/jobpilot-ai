import structlog

from app.config import get_settings
from app.llm.provider import get_llm_provider
from app.memory.qdrant_store import get_memory_store
from app.schemas.agent_state import JobPilotState
from app.utils.formatting import (
    competitive_offer_price,
    finalize_kwork_proposal,
    format_budget,
    format_offer_price,
    sanitize_proposal_text,
)

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """Write a short, concrete freelance proposal in plain text. NOT a template.

Style (critical):
- ALWAYS start with a greeting: "Здравствуйте." for Russian jobs, "Hello." for English.
- Do NOT retell or summarize the job description. The client already knows their task.
- After the greeting, open with ONE specific technical detail from the posting.
- Immediately say how you would build it: stack, architecture, key steps.
- Sound like an experienced freelancer in chat, not a cover letter.
- 80-150 words for PROPOSAL. Short paragraphs, no filler.
- No generic phrases ("готов взяться", "имею большой опыт", "качественно и в срок").
- Plain text ONLY: no markdown, no asterisks, no bold/italic
- Do NOT use em dash (—). Use commas, periods, or hyphen (-) instead
- Do NOT suggest calls or meetings. End with 1-2 clarifying questions in text.
- Write in the same language as the job posting (Russian for RU jobs)

Pricing (critical):
- If YOUR OFFER PRICE is provided, quote EXACTLY that amount once in the proposal.
- Never invent your own price. Never exceed the offer price.
- Never mention the buyer's allowable/max budget cap as your price.

Format your response as:

PROPOSAL:
<short proposal text>

EXECUTION_PLAN:
<3-4 numbered steps, one line each>

TIMELINE:
<brief milestones with durations>"""

KWORK_SYSTEM_PROMPT = """Write a selling Kwork offer (отклик) in Russian for Kwork Exchange. Each offer must be unique for THIS project.
This text is sent directly to the buyer on Kwork. One connect is spent per offer.

Kwork selling rules (from Kwork training, critical):
1. NO templates. Never reuse the same wording across projects.
2. Relevant portfolio is mandatory. Pick the closest case from DEVELOPER PORTFOLIO and explain
   what it has in common with the buyer's task (stack, domain, architecture).
3. Describe HOW you will work: tools, approach, key steps. Suggest improvements if appropriate.
4. End with 2-3 numbered clarifying questions about THIS project.
5. Do NOT brag about years of experience or total project count.
   ("8 лет опыта", "1000+ проектов" are useless without relevant examples.)
6. Do NOT use filler: "готов взяться", "имею большой опыт", "качественно и в срок",
   "уникальное предложение", "лучшие цены".
7. Do NOT retell the job description. The buyer already wrote it.
8. Plain text ONLY: no markdown, no asterisks, no bold/italic.
9. Do NOT use em dash (—). Use commas, periods, or hyphen (-) instead.
10. Do NOT suggest calls or meetings.

PROPOSAL structure (follow this order in one cohesive text):
- Greeting with buyer name if CLIENT NAME is provided ("Добрый день, Иван!").
- One sentence why this task is interesting + link to a relevant portfolio project.
- Concrete overlap between that project and the buyer's task (technologies, features).
- How you will solve it: stack, architecture, deliverables.
- YOUR OFFER PRICE quoted exactly once (if provided).
- 2-3 numbered questions specific to this posting.

Length (critical):
- PROPOSAL is sent to the buyer on Kwork. Keep it SHORT: 100-150 words, max 1200 characters.
- Kwork hard limit is 2000 characters. Buyers skim offers; long text hurts conversion.
- Do NOT pad with guarantees, filler, or repeated points.
- EXECUTION_PLAN and TIMELINE below are for internal Telegram review ONLY. They are NOT sent to Kwork.

Pricing (critical):
- If YOUR OFFER PRICE is provided, quote EXACTLY that amount once in PROPOSAL.
- Never invent your own price. Never exceed the offer price.
- Never mention the buyer's allowable/max budget cap as your price.
- Do not undercut without reason. If price is already discounted, do not apologize for it.

Format your response as:

PROPOSAL:
<full Kwork offer text sent to the buyer>

EXECUTION_PLAN:
<3-4 numbered work steps for internal review, one line each>

TIMELINE:
<brief milestones with durations>"""


class ProposalAgent:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._llm = get_llm_provider()
        self._memory = get_memory_store()

    async def run(self, state: JobPilotState) -> dict:
        job = state.get("job_data", {})
        score = state.get("score", 0)
        is_kwork = job.get("platform") == "kwork"

        job_context = f"{job.get('title', '')} {job.get('description', '')[:500]}"
        similar = await self._memory.search_similar_proposals(job_context)
        phrases = await self._memory.search_phrases(job_context)

        examples = ""
        if similar:
            label = (
                "Successful Kwork offer patterns (adapt ideas, do not copy wording):\n"
                if is_kwork
                else "Successful proposal patterns (adapt, do not copy):\n"
            )
            examples = f"\n\n{label}"
            for item in similar[:2]:
                examples += f"- {str(item.get('content', ''))[:300]}...\n"

        phrase_hint = ""
        if phrases:
            phrase_hint = f"\nHigh-conversion phrases to naturally incorporate: {', '.join(phrases[:3])}"

        budget_label = format_budget(
            job.get("budget_min"),
            job.get("budget_max"),
            job.get("budget_currency", "USD"),
            job.get("platform"),
        )

        offer_price_hint = ""
        if is_kwork:
            offer = format_offer_price(
                job.get("budget_min"),
                job.get("budget_max"),
                job.get("budget_currency", "RUB"),
                self._settings.kwork_offer_discount_percent,
            )
            if offer:
                offer_price_hint = (
                    f"\nYOUR OFFER PRICE (quote exactly once): {offer}\n"
                    f"(Competitive bid {int(self._settings.kwork_offer_discount_percent)}% "
                    f"below buyer desired budget. Do NOT quote higher. "
                    f"Do NOT mention allowable/max cap.)"
                )

        portfolio_block = ""
        if is_kwork and self._settings.developer_portfolio.strip():
            portfolio_block = f"\nPortfolio cases:\n{self._settings.developer_portfolio.strip()}\n"

        client_line = ""
        if is_kwork and job.get("client_name"):
            client_line = f"Client name: {job.get('client_name')}\n"

        user_prompt = f"""Developer:
Name: {self._settings.developer_name}
Skills: {self._settings.developer_skills}
Rate: ${self._settings.developer_hourly_rate}/hr
Bio: {self._settings.developer_bio}
{portfolio_block}
Job (score: {score}/100):
Platform: {job.get('platform', 'unknown')}
{client_line}Title: {job.get('title', '')}
Description: {job.get('description', '')}
Budget: {budget_label}{offer_price_hint}
Skills: {', '.join(job.get('skills', []))}
Deadline: {job.get('deadline', 'Not specified')}
{examples}{phrase_hint}

Write a completely unique {"Kwork offer" if is_kwork else "proposal"} for this specific job."""

        system_prompt = KWORK_SYSTEM_PROMPT if is_kwork else SYSTEM_PROMPT
        temperature = 0.85 if is_kwork else 0.8

        try:
            response = await self._llm.complete(system_prompt, user_prompt, temperature=temperature)
            parsed = self._parse_response(response)
            parsed["proposal"] = self._finalize_proposal(
                sanitize_proposal_text(parsed["proposal"]),
                job,
            )
            parsed["execution_plan"] = sanitize_proposal_text(parsed["execution_plan"])
            parsed["timeline"] = sanitize_proposal_text(parsed["timeline"])
            proposal_len = len(parsed["proposal"])
            if is_kwork and proposal_len > 1200:
                logger.warning(
                    "Kwork proposal exceeds recommended length",
                    title=job.get("title"),
                    length=proposal_len,
                    recommended_max=1200,
                )
            logger.info(
                "ProposalAgent generated proposal",
                title=job.get("title"),
                platform=job.get("platform"),
                proposal_length=proposal_len if is_kwork else None,
            )
            return {
                "proposal_content": parsed["proposal"],
                "execution_plan": parsed["execution_plan"],
                "timeline": parsed["timeline"],
            }
        except Exception as exc:
            logger.error("ProposalAgent failed", error=str(exc))
            return {
                "proposal_content": "",
                "execution_plan": "",
                "timeline": "",
                "error": str(exc),
            }

    def _parse_response(self, response: str) -> dict:
        proposal = response
        execution_plan = ""
        timeline = ""

        if "PROPOSAL:" in response:
            parts = response.split("EXECUTION_PLAN:")
            proposal = parts[0].replace("PROPOSAL:", "").strip()
            if len(parts) > 1:
                rest = parts[1]
                if "TIMELINE:" in rest:
                    ep_parts = rest.split("TIMELINE:")
                    execution_plan = ep_parts[0].strip()
                    timeline = ep_parts[1].strip()
                else:
                    execution_plan = rest.strip()

        return {
            "proposal": proposal,
            "execution_plan": execution_plan,
            "timeline": timeline,
        }

    def _is_russian_job(self, job: dict) -> bool:
        sample = f"{job.get('title', '')} {job.get('description', '')[:200]}"
        return any("\u0400" <= ch <= "\u04ff" for ch in sample)

    def _finalize_proposal(self, text: str, job: dict) -> str:
        if not text:
            return text

        if job.get("platform") == "kwork":
            offer = competitive_offer_price(
                job.get("budget_min"),
                job.get("budget_max"),
                self._settings.kwork_offer_discount_percent,
            )
            return finalize_kwork_proposal(
                text,
                client_name=job.get("client_name"),
                offer_price=offer,
                currency=job.get("budget_currency", "RUB"),
            )

        lower = text.lower()
        if self._is_russian_job(job):
            if not any(lower.startswith(g) for g in ("здравствуйте", "добрый", "привет")):
                text = f"Здравствуйте. {text}"
        elif not any(lower.startswith(g) for g in ("hello", "hi", "hey", "good ")):
            text = f"Hello. {text}"

        return text


async def proposal_node(state: JobPilotState) -> dict:
    agent = ProposalAgent()
    return await agent.run(state)
