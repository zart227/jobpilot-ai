"""Queue one proposal review reissue + deliver outbox (dry-run stack smoke)."""

from __future__ import annotations

import asyncio
import sys
import uuid

from app.config import get_settings
from app.services.review_reissue import reissue_review


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: ops_queue_review_reissue.py <telegram_pending_uuid>", file=sys.stderr)
        return 2
    pending_id = uuid.UUID(sys.argv[1])
    settings = get_settings()
    chat_raw = settings.telegram_admin_chat_id.strip()
    if not chat_raw:
        print("TELEGRAM_ADMIN_CHAT_ID required", file=sys.stderr)
        return 1
    chat_id = int(chat_raw)
    user_raw = settings.telegram_admin_user_id.strip()
    user_id = int(user_raw) if user_raw else chat_id

    result = await reissue_review(
        "proposal",
        pending_id,
        chat_id=chat_id,
        user_id=user_id,
    )
    print(
        {
            "action": result.action,
            "pending_id": str(result.pending_id) if result.pending_id else None,
            "outbox_id": str(result.outbox_id) if result.outbox_id else None,
            "reason": result.reason,
        }
    )
    if result.action != "queued":
        return 1

    from app.tasks.scrape_tasks import deliver_notification_outbox_task

    delivery = deliver_notification_outbox_task.delay().get(timeout=120)
    print({"outbox_delivery": delivery})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
