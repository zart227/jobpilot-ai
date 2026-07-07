import json
import re

import structlog

from app.llm.provider import get_simple_llm_provider
from app.schemas.agent_state import JobPilotState

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """Handle client replies to freelance proposals.

Classify intent and respond concisely.

Respond ONLY with valid JSON:
{
  "intent": "question|negotiation|acceptance|rejection|clarification|other",
  "reply": "your concise professional response (max 150 words)"
}

Do NOT use em dash (—) in replies. Use commas or periods instead.

Be helpful, professional, and move toward closing the deal when appropriate."""


class ChatAgent:
    def __init__(self) -> None:
        self._llm = get_simple_llm_provider()

    async def run(self, state: JobPilotState) -> dict:
        job = state.get("job_data", {})
        client_message = state.get("client_message", "")
        proposal = state.get("proposal_content") or state.get("edited_proposal", "")

        if not client_message:
            return {"chat_intent": "none", "chat_reply": ""}

        user_prompt = f"""Job context:
Title: {job.get('title', '')}
Platform: {job.get('platform', '')}

Original proposal sent:
{proposal[:1500]}

Client message:
{client_message}

Classify intent and draft a reply."""

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
