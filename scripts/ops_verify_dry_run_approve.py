#!/usr/bin/env python3
"""After Telegram APPROVE with PLATFORM_DRY_RUN=true, verify send_attempt markers."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

from sqlalchemy import select


async def main() -> int:
    pending_id = uuid.UUID(sys.argv[1]) if len(sys.argv) > 1 else None
    if pending_id is None:
        print("Usage: ops_verify_dry_run_approve.py <telegram_pending_uuid>", file=sys.stderr)
        return 2
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://jobpilot:jobpilot_secret@127.0.0.1:5434/jobpilot_ai",
    )
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db.models import SendAttempt, TelegramPending
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        pending = await session.get(TelegramPending, pending_id)
        if not pending:
            print({"ok": False, "reason": "pending_not_found"})
            return 1
        attempts = (
            await session.scalars(
                select(SendAttempt)
                .where(SendAttempt.proposal_id == pending.proposal_id)
                .order_by(SendAttempt.created_at.desc())
                .limit(3)
            )
        ).all()
        summary = {
            "ok": False,
            "pending_status": pending.status,
            "pending_message_id": pending.message_id,
            "platform_dry_run": get_settings().platform_dry_run,
            "recent_attempts": [
                {
                    "id": str(a.id),
                    "status": a.status,
                    "kind": a.kind,
                    "platform_response": a.platform_response or {},
                    "snapshot_metadata": a.snapshot_metadata or {},
                }
                for a in attempts
            ],
        }
        latest = attempts[0] if attempts else None
        dry_ok = bool(
            latest
            and (latest.platform_response or {}).get("dry_run") is True
            and get_settings().platform_dry_run
        )
        summary["ok"] = pending.status == "approved" and dry_ok
        print(summary)
        if pending.status == "pending":
            print("Hint: press APPROVE on the Telegram card first.", file=sys.stderr)
            return 1
        if not dry_ok:
            print("Hint: expected platform_response.dry_run=true with PLATFORM_DRY_RUN=true.", file=sys.stderr)
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
