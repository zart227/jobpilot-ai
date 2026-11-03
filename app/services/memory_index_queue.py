"""Durable SQL queue for deferred Qdrant indexing (proposals, phrases, edit prefs)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from app.config import Settings, get_settings
from app.db.models import MemoryIndexPending
from app.db.session import AsyncSessionLocal
from app.memory.qdrant_store import get_memory_store

logger = structlog.get_logger(__name__)

KIND_SUCCESSFUL_PROPOSAL = "successful_proposal"
KIND_PRICING_PHRASE = "pricing_phrase"
KIND_EDIT_PREFERENCE = "edit_preference"

CLAIM_OWNER = "memory_index_worker"


async def enqueue_memory_index(kind: str, dedupe_key: str, payload: dict[str, Any]) -> bool:
    """Persist one deferred index operation; refresh payload when dedupe key already exists."""
    if not dedupe_key or len(dedupe_key) > 512:
        return False
    async with AsyncSessionLocal() as session:
        stmt = insert(MemoryIndexPending).values(
            id=uuid.uuid4(),
            kind=kind,
            dedupe_key=dedupe_key,
            payload=payload,
        )
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=[MemoryIndexPending.dedupe_key],
            set_={
                "kind": excluded.kind,
                "payload": excluded.payload,
                "last_error": None,
                "claim_token": None,
                "claim_expires_at": None,
                "claim_owner": None,
            },
            where=MemoryIndexPending.payload.is_distinct_from(excluded.payload),
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0


async def _claim_memory_index_batch(
    settings: Settings,
    *,
    limit: int,
) -> list[MemoryIndexPending]:
    now = datetime.now(UTC)
    lease_until = now + timedelta(seconds=settings.memory_index_lease_seconds)
    claimed: list[MemoryIndexPending] = []

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MemoryIndexPending)
            .where(
                or_(
                    MemoryIndexPending.claim_expires_at.is_(None),
                    MemoryIndexPending.claim_expires_at < now,
                ),
            )
            .order_by(MemoryIndexPending.created_at.asc(), MemoryIndexPending.id.asc())
            .limit(max(0, limit))
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.claim_token = uuid.uuid4()
            row.claim_expires_at = lease_until
            row.claim_owner = CLAIM_OWNER
            claimed.append(row)
        if claimed:
            await session.commit()
        else:
            await session.rollback()
    return claimed


async def _finalize_memory_index_success(
    pending_id: uuid.UUID,
    claim_token: uuid.UUID,
) -> bool:
    async with AsyncSessionLocal() as session:
        row = await session.get(MemoryIndexPending, pending_id, with_for_update=True)
        if not row or not claim_token or row.claim_token != claim_token:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def _finalize_memory_index_failure(
    pending_id: uuid.UUID,
    claim_token: uuid.UUID,
    error: str,
) -> bool:
    async with AsyncSessionLocal() as session:
        row = await session.get(MemoryIndexPending, pending_id, with_for_update=True)
        if not row or not claim_token or row.claim_token != claim_token:
            return False
        row.last_error = error[:2000]
        row.claim_token = None
        row.claim_expires_at = None
        row.claim_owner = None
        await session.commit()
        return True


async def recover_stale_memory_index_claims(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MemoryIndexPending).where(
                MemoryIndexPending.claim_expires_at.is_not(None),
                MemoryIndexPending.claim_expires_at < now,
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.claim_token = None
            row.claim_expires_at = None
            row.claim_owner = None
        if rows:
            await session.commit()
        else:
            await session.rollback()
        return len(rows)


async def process_memory_index_queue(limit: int = 25) -> dict[str, int]:
    settings = get_settings()
    await recover_stale_memory_index_claims(settings)
    claimed = await _claim_memory_index_batch(settings, limit=limit)
    if not claimed:
        return {"memory_queue_processed": 0, "memory_queue_succeeded": 0, "memory_queue_failed": 0}

    memory = await asyncio.to_thread(get_memory_store)
    processed = succeeded = failed = 0

    for row in claimed:
        processed += 1
        claim_token = row.claim_token
        if not claim_token:
            failed += 1
            continue
        try:
            ok = await _apply_pending_row(memory, row.kind, row.payload)
        except Exception as exc:
            ok = False
            error = str(exc)
        else:
            error = None if ok else "index deferred"

        if ok:
            if await _finalize_memory_index_success(row.id, claim_token):
                succeeded += 1
            else:
                failed += 1
        elif await _finalize_memory_index_failure(row.id, claim_token, error or "index deferred"):
            failed += 1
        else:
            failed += 1

    return {
        "memory_queue_processed": processed,
        "memory_queue_succeeded": succeeded,
        "memory_queue_failed": failed,
    }


async def _apply_pending_row(memory, kind: str, payload: dict[str, Any]) -> bool:
    if kind == KIND_SUCCESSFUL_PROPOSAL:
        return await memory.store_successful_proposal(
            payload["memory_key"],
            payload["content"],
            payload.get("metadata"),
        )
    if kind == KIND_PRICING_PHRASE:
        return await memory.store_pricing_phrase(payload["phrase"], float(payload["score"]))
    if kind == KIND_EDIT_PREFERENCE:
        return await memory.store_edit_preference(
            instruction=payload["instruction"],
            original_content=payload["original_content"],
            edited_content=payload["edited_content"],
            metadata=payload.get("metadata"),
        )
    logger.warning("Unknown memory index kind", kind=kind)
    return False
