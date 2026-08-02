import json
import re

import structlog

from app.llm.provider import get_simple_llm_provider
from app.schemas.agent_state import JobPilotState

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You help a freelancer reply to client messages on Kwork.

Use the job posting, the proposal we already sent, and the client's new message.
Answer in the same language as the client (usually Russian).
Address the client's specific points; do not give a generic reply.

Classify intent and draft a concise professional reply.

Respond ONLY with valid JSON:
{
  "intent": "question|negotiation|acceptance|rejection|clarification|other",
  "reply": "your reply (max 200 words)"
}

Do NOT use em dash (—) in replies. Use commas or periods instead."""


def build_chat_user_prompt(
    job: dict,
    proposal: str,
    client_message: str,
) -> str:
    lines = [
        "=== Задача (заказ) ===",
        f"Название: {job.get('title', '')}",
    ]

    description = str(job.get("description", "")).strip()
    if description:
        lines.append(f"Описание:\n{description[:2500]}")

    budget_min = job.get("budget_min")
    budget_max = job.get("budget_max")
    currency = job.get("budget_currency") or "RUB"
    if budget_min or budget_max:
        lines.append(f"Бюджет: {budget_min or '?'} – {budget_max or '?'} {currency}")

    skills = job.get("skills") or []
    if skills:
        lines.append(f"Навыки: {', '.join(str(skill) for skill in skills[:20])}")

    lines.append("\n=== Наш отклик на эту задачу ===")
    lines.append(proposal[:2500] if proposal.strip() else "(отклик не найден в базе)")

    lines.append("\n=== Сообщение клиента ===")
    lines.append(client_message[:2500])

    lines.append(
        "\nС учётом задачи, нашего отклика и сообщения клиента: "
        "классифицируй интент и составь ответ."
    )
    return "\n".join(lines)


class ChatAgent:
    def __init__(self) -> None:
        self._llm = get_simple_llm_provider()

    async def run(self, state: JobPilotState) -> dict:
        job = state.get("job_data", {})
        client_message = state.get("client_message", "")
        proposal = state.get("proposal_content") or state.get("edited_proposal", "")

        if not client_message:
            return {"chat_intent": "none", "chat_reply": ""}

        user_prompt = build_chat_user_prompt(job, proposal, client_message)

        try:
            response = await self._llm.complete(SYSTEM_PROMPT, user_prompt, temperature=0.5)
            parsed = self._parse_response(response)
            logger.info("ChatAgent response", intent=parsed["intent"])
            return {
                "chat_intent": parsed["intent"],
                "chat_reply": parsed["reply"],
            }
        except Exception as exc:
            logger.error("ChatAgent failed", error=str(exc))
            return {"chat_intent": "error", "chat_reply": "", "error": str(exc)}

    def _parse_response(self, response: str) -> dict:
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return {
                "intent": str(data.get("intent", "other")),
                "reply": str(data.get("reply", "")),
            }
        return {"intent": "other", "reply": response[:500]}


async def chat_node(state: JobPilotState) -> dict:
    agent = ChatAgent()
    return await agent.run(state)
