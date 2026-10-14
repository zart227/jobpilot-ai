"""Compact conversation context for inbox LLM prompts (summary + cache)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import structlog

from app.agents.chat_agent import format_conversation_history
from app.db.models import Job
from app.llm.provider import get_simple_llm_provider

logger = structlog.get_logger(__name__)

SUMMARY_CACHE_KEY = "inbox_conversation_summary"
RECENT_TURNS = 2
SHORT_MAX_TURNS = 2
SHORT_MAX_CHARS = 400
SUMMARY_MAX_CHARS = 700

SUMMARY_SYSTEM_PROMPT = """You summarize a freelancer-client chat on Kwork.

Write 2-4 short sentences in Russian.
Include only facts: questions, answers, agreements, objections, deadlines, price.
No greetings filler, no quotes, no bullet lists. Plain text only."""


def _turns_char_count(turns: list[dict[str, str]]) -> int:
    return sum(len(str(turn.get("text", ""))) for turn in turns)


def _get_summary_cache(job: Job) -> dict:
    raw = job.raw_data or {}
    cached = raw.get(SUMMARY_CACHE_KEY)
    return cached if isinstance(cached, dict) else {}


def _older_turns_fingerprint(turns: list[dict[str, str]]) -> str:
    payload = json.dumps(turns, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def _save_summary_cache(
    session,
    job: Job,
    *,
    older_turn_count: int,
    older_turn_fingerprint: str,
    text: str,
) -> None:
    raw = dict(job.raw_data or {})
    raw[SUMMARY_CACHE_KEY] = {
        "older_turn_count": older_turn_count,
        "older_turn_fingerprint": older_turn_fingerprint,
        "text": text.strip(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    job.raw_data = raw
    await session.commit()


def _format_turns_for_summary(turns: list[dict[str, str]]) -> str:
    role_labels = {"client": "Клиент", "freelancer": "Вы"}
    lines: list[str] = []
    for turn in turns:
        role = role_labels.get(turn.get("role", ""), "Участник")
        text = str(turn.get("text", "")).strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


async def summarize_turns(turns: list[dict[str, str]]) -> str:
    if not turns:
        return ""

    llm = get_simple_llm_provider()
    transcript = _format_turns_for_summary(turns)
    if len(transcript) > 6000:
        transcript = transcript[:5997] + "..."

    user_prompt = f"Переписка:\n{transcript}\n\nКраткое содержание:"
    summary = await llm.complete(SUMMARY_SYSTEM_PROMPT, user_prompt, temperature=0.2)
    summary = summary.strip()
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[: SUMMARY_MAX_CHARS - 3] + "..."
    return summary


async def build_conversation_context(
    session,
    job: Job,
    turns: list[dict[str, str]],
) -> str:
    if not turns:
        return "(ранее сообщений не было)"

    if len(turns) <= SHORT_MAX_TURNS and _turns_char_count(turns) <= SHORT_MAX_CHARS:
        return format_conversation_history(turns, max_turns=SHORT_MAX_TURNS, max_chars=SHORT_MAX_CHARS)

    if len(turns) <= RECENT_TURNS:
        return format_conversation_history(turns, max_turns=RECENT_TURNS, max_chars=1500)

    older = turns[:-RECENT_TURNS]
    recent = turns[-RECENT_TURNS:]
    older_fp = _older_turns_fingerprint(older)
    cached = _get_summary_cache(job)
    if (
        cached.get("older_turn_count") == len(older)
        and cached.get("older_turn_fingerprint") == older_fp
        and cached.get("text")
    ):
        summary = str(cached["text"])
    else:
        try:
            summary = await summarize_turns(older)
            if summary and session is not None:
                await _save_summary_cache(
                    session,
                    job,
                    older_turn_count=len(older),
                    older_turn_fingerprint=older_fp,
                    text=summary,
                )
        except Exception as exc:
            logger.warning("Conversation summary failed, using truncated history", error=str(exc))
            return format_conversation_history(turns, max_turns=6, max_chars=1200)

    recent_block = format_conversation_history(recent, max_turns=RECENT_TURNS, max_chars=1500)
    if not summary:
        return recent_block
    return f"Кратко о более ранней переписке:\n{summary}\n\nПоследние сообщения:\n{recent_block}"
