"""Send Kwork proposals already approved in Telegram but not delivered.

Usage:
    python scripts/send_approved_proposals.py --dry-run
    python scripts/send_approved_proposals.py --limit 5
    python scripts/send_approved_proposals.py
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.services.kwork_pause import get_kwork_pause_reason, is_kwork_paused
from app.services.proposal_sender import ProposalSender
from app.telegram.bot import format_send_outcome_message
from app.services.kwork_browser import is_kwork_connects_limit_error, is_kwork_order_closed_error
from app.utils.formatting import extract_site_order_id
from app.utils.proxy import send_telegram_message


async def list_approved_unsent() -> list[tuple[uuid.UUID, uuid.UUID, str, str | None]]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TelegramPending, Job)
            .join(Job, Job.id == TelegramPending.job_id)
            .join(Proposal, Proposal.id == TelegramPending.proposal_id)
            .where(
                TelegramPending.status == "approved",
                Job.platform == "kwork",
            )
            .order_by(TelegramPending.job_id, TelegramPending.created_at.desc())
        )
        rows = result.all()

    sent_job_ids: set[uuid.UUID] = set()
    skipped_job_ids: set[uuid.UUID] = set()
    async with AsyncSessionLocal() as session:
        sent_result = await session.execute(
            select(Proposal.job_id).where(Proposal.status == "sent")
        )
        sent_job_ids = {row[0] for row in sent_result.all()}
        skipped_result = await session.execute(
            select(Proposal.job_id).where(Proposal.status == "skipped")
        )
        skipped_job_ids = {row[0] for row in skipped_result.all()}

    seen_jobs: set[uuid.UUID] = set()
    items: list[tuple[uuid.UUID, uuid.UUID, str, str | None]] = []
    for pending, job in rows:
        if job.id in sent_job_ids or job.id in skipped_job_ids or job.id in seen_jobs:
            continue
        seen_jobs.add(job.id)
        order_id = extract_site_order_id(
            external_id=job.external_id,
            url=job.url,
            platform=job.platform,
        )
        items.append((job.id, pending.proposal_id, job.title, order_id))
    return items


async def send_one(
    job_id: uuid.UUID,
    proposal_id: uuid.UUID,
    *,
    dry_run: bool,
) -> tuple[bool, str | None]:
    async with AsyncSessionLocal() as session:
        proposal = await session.get(Proposal, proposal_id)
        if not proposal:
            return False, "Отклик не найден"
        content = proposal.content

    if dry_run:
        return True, None

    sender = ProposalSender()
    success, error, _debug = await sender.send(
        str(job_id),
        str(proposal_id),
        content,
    )
    return success, error


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send approved Kwork proposals that failed to deliver",
    )
    parser.add_argument("--dry-run", action="store_true", help="List only, do not send")
    parser.add_argument("--limit", type=int, default=0, help="Max jobs to process (0 = all)")
    parser.add_argument(
        "--delay",
        type=float,
        default=0,
        help="Fixed seconds between sends (overrides random range if > 0)",
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=20.0,
        help="Min random pause between sends in seconds (default: 20)",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=90.0,
        help="Max random pause between sends in seconds (default: 90)",
    )
    args = parser.parse_args()

    pause_reason = get_kwork_pause_reason()
    if pause_reason:
        print(f"⏸ {pause_reason}")
        return

    items = await list_approved_unsent()
    if args.limit > 0:
        items = items[: args.limit]

    if not items:
        print("Нет одобренных неотправленных заказов.")
        return

    print(f"К отправке: {len(items)} заказов")
    for index, (job_id, proposal_id, title, order_id) in enumerate(items, start=1):
        suffix = f" №{order_id}" if order_id else ""
        print(f"  {index}. {title}{suffix} ({job_id})")

    if args.dry_run:
        print("\nDry-run: отправка не выполнялась.")
        return

    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_CHAT_ID are required")

    chat_id = int(settings.telegram_admin_chat_id)
    ok_count = 0
    fail_count = 0
    skip_count = 0
    notify_fail_count = 0
    stop_batch = False

    for index, (job_id, proposal_id, title, order_id) in enumerate(items, start=1):
        suffix = f"\n№ заказа: {order_id}" if order_id else ""
        print(f"\n[{index}/{len(items)}] Отправка: {title}{suffix}")

        success, error = await send_one(job_id, proposal_id, dry_run=False)

        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)

        if success:
            ok_count += 1
            print("  ✅ OK")
        elif is_kwork_order_closed_error(error):
            skip_count += 1
            print(f"  ⏭ {error}")
        elif is_kwork_connects_limit_error(error):
            fail_count += 1
            print(f"  ❌ {error}")
            print("  🛑 Лимит коннектов исчерпан — остальные отклики не отправляем.")
            stop_batch = True
        else:
            fail_count += 1
            print(f"  ❌ {error}")

        try:
            await send_telegram_message(
                settings.telegram_bot_token,
                chat_id,
                format_send_outcome_message(
                    job,
                    success=success,
                    send_error=error,
                ),
                settings=settings,
                parse_mode="HTML" if not success else None,
                disable_web_page_preview=not success,
            )
        except Exception as exc:
            notify_fail_count += 1
            print(f"  ⚠️ Telegram: {exc}")

        if index < len(items) and not stop_batch:
            if args.delay > 0:
                pause = args.delay
            else:
                low = min(args.delay_min, args.delay_max)
                high = max(args.delay_min, args.delay_max)
                pause = random.uniform(low, high)
            print(f"  ⏳ пауза {pause:.0f} сек...")
            await asyncio.sleep(pause)

        if stop_batch:
            break

    print(
        f"\nГотово: успешно {ok_count}, пропущено {skip_count}, "
        f"ошибок {fail_count}, уведомлений не доставлено {notify_fail_count}"
    )


if __name__ == "__main__":
    asyncio.run(main())
