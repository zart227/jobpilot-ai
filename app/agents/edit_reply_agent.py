import structlog

from app.agents.chat_agent import format_conversation_history
from app.llm.errors import classify_llm_error
from app.llm.provider import get_llm_provider
from app.utils.formatting import sanitize_proposal_text

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You edit a freelance chat reply based on the user's instruction.

Context: job posting, our proposal, conversation summary, the client's latest message, and the current draft.

Rules:
- Apply ONLY what the user asked.
- Keep the same language as the original (Russian for Kwork).
- Stay consistent with the job, our proposal, and prior messages in the thread.
- Plain text ONLY: no markdown, no asterisks.
- Do NOT use em dash (—). Use commas or periods instead.
- Be professional and concise (max 200 words unless user asks otherwise).
- Return ONLY the revised reply text, no labels or commentary."""


class EditReplyAgent:
    def __init__(self) -> None:
        self._llm = get_llm_provider()

    async def run(
        self,
        *,
        current_reply: str,
        instruction: str,
        client_message: str,
        job_title: str = "",
        job_description: str = "",
        proposal_content: str = "",
        conversation_context: str = "",
        conversation_history: list[dict[str, str]] | None = None,
    ) -> str:
        history_block = conversation_context.strip()
        if not history_block and conversation_history:
            history_block = format_conversation_history(conversation_history)

        user_prompt = f"""Job title: {job_title}

Job description:
{job_description[:2000]}

Our proposal to this job:
{proposal_content[:2000] if proposal_content.strip() else "(not available)"}

Conversation summary:
{history_block or "(no prior messages)"}

Client's latest message:
{client_message[:1500]}

Current reply draft:
{current_reply}

User instruction:
{instruction}

Return the full revised reply."""

        try:
            response = await self._llm.complete(SYSTEM_PROMPT, user_prompt, temperature=0.5)
            edited = sanitize_proposal_text(response.strip())
            logger.info(
                "EditReplyAgent revised reply",
                instruction_preview=instruction[:80],
                length=len(edited),
            )
            return edited
        except Exception as exc:
            logger.error("EditReplyAgent failed", error=str(exc))
            raise classify_llm_error(exc) from exc
