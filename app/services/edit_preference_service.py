import asyncio
import uuid
from typing import Any

import structlog

from app.db.models import ProposalEdit
from app.db.session import AsyncSessionLocal
from app.memory.qdrant_store import get_memory_store

logger = structlog.get_logger(__name__)


class EditPreferenceService:
    def __init__(self) -> None:
        self._memory = None

    async def record_edit_steps(
        self,
        *,
        proposal_id: uuid.UUID,
        job_id: uuid.UUID,
        platform: str,
        job_context: str,
        edit_steps: list[dict[str, str]],
    ) -> None:
        if not edit_steps:
            return

        async with AsyncSessionLocal() as session:
            for step in edit_steps:
                session.add(
                    ProposalEdit(
                        proposal_id=proposal_id,
                        job_id=job_id,
                        instruction=step["instruction"],
                        original_content=step["before"],
                        edited_content=step["after"],
                        platform=platform,
                    )
                )
            await session.commit()

        from app.services.memory_index_queue import KIND_EDIT_PREFERENCE, enqueue_memory_index

        async def queue_step(step: dict[str, str]) -> None:
            metadata = {
                "proposal_id": str(proposal_id),
                "platform": platform,
                "job_context": job_context[:500],
            }
            dedupe_key = (
                f"edit:{proposal_id}:{step['instruction'][:80]}:"
                f"{step['before'][:40]}:{step['after'][:40]}"
            )
            await enqueue_memory_index(
                KIND_EDIT_PREFERENCE,
                dedupe_key,
                {
                    "instruction": step["instruction"],
                    "original_content": step["before"],
                    "edited_content": step["after"],
                    "metadata": metadata,
                },
            )

        try:
            if self._memory is None:
                self._memory = await asyncio.to_thread(get_memory_store)
            for step in edit_steps:
                stored = await self._memory.store_edit_preference(
                    instruction=step["instruction"],
                    original_content=step["before"],
                    edited_content=step["after"],
                    metadata={
                        "proposal_id": str(proposal_id),
                        "platform": platform,
                        "job_context": job_context[:500],
                    },
                )
                if not stored:
                    await queue_step(step)
        except Exception as exc:
            logger.warning("Edit preferences saved in DB but vector update failed", error=str(exc))
            for step in edit_steps:
                await queue_step(step)

        logger.info(
            "Recorded proposal edit preferences",
            proposal_id=str(proposal_id),
            steps=len(edit_steps),
        )

    async def get_style_hints(self, job_context: str, limit: int = 5) -> list[dict[str, Any]]:
        try:
            if self._memory is None:
                self._memory = await asyncio.to_thread(get_memory_store)
            return await self._memory.search_edit_preferences(job_context, limit=limit)
        except Exception as exc:
            logger.warning("Edit preference lookup skipped", error=str(exc))
            return []
