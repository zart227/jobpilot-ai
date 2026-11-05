import structlog

from app.llm.provider import get_llm_provider
from app.schemas.agent_state import JobPilotState
from app.schemas.llm_outputs import parse_chat_output
from app.services.pipeline_llm_ledger import append_stage_log

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You help a freelancer reply to client messages on Kwork.

Use the job posting, our proposal, the conversation summary, and the client's latest message.
Answer in the same language as the client (usually Russian).
Address the client's specific points; stay consistent with prior messages in the thread.
Do not repeat what was already said in our previous replies unless the client asks again.

Classify intent and draft a concise professional reply.

Respond ONLY with valid JSON:
{
  "intent": "question|negotiation|acceptance|rejection|clarification|other",
  "reply": "your reply (max 200 words)"
}

Do NOT use em dash (—) in replies. Use commas or periods instead."""


def format_conversation_history(
    turns: list[dict[str, str]] | None,
    *,
    max_turns: int = 20,
    max_chars: int = 4000,
) -> str:
    if not turns:
        return "(ранее сообщений не было)"

    role_labels = {
        "client": "Клиент",
        "freelancer": "Вы",
    }
    lines: list[str] = []
    total_chars = 0
    for turn in turns[-max_turns:]:
        role = role_labels.get(turn.get("role", ""), "Участник")
        text = str(turn.get("text", "")).strip()
        if not text:
            continue
        line = f"{role}: {text}"
        if total_chars + len(line) > max_chars:
            remaining = max_chars - total_chars
            if remaining > 40:
                lines.append(line[: remaining - 3] + "...")
            break
        lines.append(line)
        total_chars += len(line) + 1
    return "\n".join(lines) if lines else "(ранее сообщений не было)"


def build_chat_user_prompt(
    job: dict,
    proposal: str,
    client_message: str,
    conversation_context: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
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

    lines.append("\n=== История переписки ===")
    if conversation_context is not None:
        lines.append(conversation_context.strip() or "(ранее сообщений не было)")
    else:
        lines.append(format_conversation_history(conversation_history))

    lines.append("\n=== Последнее сообщение клиента ===")
    lines.append(client_message[:2500])

    lines.append(
        "\nС учётом задачи, нашего отклика, истории переписки и последнего сообщения клиента: "
        "классифицируй интент и составь ответ."
    )
    return "\n".join(lines)


class ChatAgent:
    def __init__(self) -> None:
        self._llm = get_llm_provider()

    async def run(self, state: JobPilotState) -> dict:
        job = state.get("job_data", {})
        client_message = state.get("client_message", "")
        proposal = state.get("proposal_content") or state.get("edited_proposal", "")

        if not client_message:
            return {"chat_intent": "none", "chat_reply": ""}

        user_prompt = build_chat_user_prompt(
            job,
            proposal,
            client_message,
            conversation_context=state.get("conversation_context"),
            conversation_history=state.get("conversation_history") or [],
        )

        try:
            response = await self._llm.complete(SYSTEM_PROMPT, user_prompt, temperature=0.5)
            parsed = parse_chat_output(response)
            logger.info("ChatAgent response", intent=parsed.intent)
            return {
                "chat_intent": parsed.intent,
                "chat_reply": parsed.reply,
                "llm_stage_log": append_stage_log(
                    state,
                    "chat",
                    "main",
                    usage=self._llm.consume_last_call_usage(),
                ),
            }
        except Exception as exc:
            logger.error("ChatAgent failed", error=str(exc))
            from app.services.pipeline_processing_errors import pipeline_error_from_exception

            return {
                **pipeline_error_from_exception(exc, stage="chat"),
                "chat_intent": "error",
                "chat_reply": "",
            }


async def chat_node(state: JobPilotState) -> dict:
    agent = ChatAgent()
    return await agent.run(state)
