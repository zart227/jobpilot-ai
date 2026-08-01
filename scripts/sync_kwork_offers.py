"""Fetch sent Kwork offers and sync JobPilot DB; optionally refresh browser session.

Usage:
    python scripts/sync_kwork_offers.py
    python scripts/sync_kwork_offers.py --refresh-session
    python scripts/sync_kwork_offers.py --project-ids 3212667,3212600
"""

from __future__ import annotations

import argparse
import asyncio
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select

from app.config import get_settings
from app.db.models import Job, Proposal, TelegramPending
from app.db.session import AsyncSessionLocal
from app.services.kwork_browser import (
    _is_access_blocked,
    close_browser,
    launch_browser,
    login,
)
from app.utils.proxy import get_proxy_candidates, mask_proxy

OFFERS_URLS = (
    "https://kwork.ru/manage_orders?tab=offers",
    "https://kwork.ru/projects?tab=offers",
)
PROJECT_ID_RE = re.compile(r"/projects/(\d+)|project=(\d+)")


async def fetch_offer_project_ids(page) -> set[str]:
    project_ids: set[str] = set()
    for offers_url in OFFERS_URLS:
        await page.goto(offers_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        if await _is_access_blocked(page) or "not_access.php" in page.url:
            raise RuntimeError(f"Kwork blocked access at {page.url}")

        for _ in range(8):
            html = await page.content()
            for match in PROJECT_ID_RE.finditer(html):
                project_id = match.group(1) or match.group(2)
                if project_id:
                    project_ids.add(project_id)
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1200)

    return project_ids


async def refresh_session(page, output: Path) -> bool:
    settings = get_settings()
    logged_in = await login(page, settings.kwork_email, settings.kwork_password)
    if not logged_in or await _is_access_blocked(page):
        return False

    output.parent.mkdir(parents=True, exist_ok=True)
    await page.context.storage_state(path=str(output))
    return True


async def sync_project_ids(project_ids: set[str]) -> list[dict[str, str]]:
    synced: list[dict[str, str]] = []
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        for project_id in sorted(project_ids, key=int):
            external_id = f"kwork-{project_id}"
            result = await session.execute(
                select(Job).where(
                    Job.platform == "kwork",
                    or_(
                        Job.external_id == external_id,
                        Job.url.ilike(f"%/projects/{project_id}%"),
                    ),
                )
            )
            job = result.scalar_one_or_none()
            if not job:
                synced.append(
                    {
                        "project_id": project_id,
                        "status": "not_in_db",
                        "title": "",
                    }
                )
                continue

            proposal_result = await session.execute(
                select(Proposal)
                .where(Proposal.job_id == job.id)
                .order_by(
                    (Proposal.status != "sent").desc(),
                    Proposal.updated_at.desc(),
                )
            )
            proposals = list(proposal_result.scalars().all())
            if not proposals:
                synced.append(
                    {
                        "project_id": project_id,
                        "status": "no_proposal",
                        "title": job.title,
                    }
                )
                continue

            pending_result = await session.execute(
                select(TelegramPending)
                .where(
                    TelegramPending.job_id == job.id,
                    TelegramPending.status.in_(("approved", "edited", "duplicate")),
                )
                .order_by(TelegramPending.created_at.desc())
            )
            pending = pending_result.scalar_one_or_none()

            proposal = None
            if pending:
                proposal = next(
                    (item for item in proposals if item.id == pending.proposal_id),
                    None,
                )
            if proposal is None:
                proposal = next(
                    (item for item in proposals if item.status in ("approved", "edited")),
                    proposals[0],
                )

            if proposal.status == "sent" and job.status == "sent":
                synced.append(
                    {
                        "project_id": project_id,
                        "status": "already_sent",
                        "title": job.title,
                    }
                )
                continue

            proposal.status = "sent"
            proposal.sent_at = proposal.sent_at or now
            job.status = "sent"
            synced.append(
                {
                    "project_id": project_id,
                    "status": "synced",
                    "title": job.title,
                    "proposal_id": str(proposal.id),
                }
            )

        await session.commit()

    return synced


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Kwork sent offers with JobPilot DB")
    parser.add_argument(
        "--refresh-session",
        action="store_true",
        help="Save a fresh Playwright storage state after successful login",
    )
    parser.add_argument(
        "--project-ids",
        default="",
        help="Comma-separated Kwork project IDs (skip live fetch)",
    )
    args = parser.parse_args()

    settings = get_settings()
    project_ids: set[str] = set()
    session_refreshed = False

    if args.project_ids.strip():
        project_ids = {item.strip() for item in args.project_ids.split(",") if item.strip()}
    else:
        proxies = get_proxy_candidates(settings, "openai")
        last_error: Exception | None = None
        for proxy in proxies:
            playwright = browser = None
            try:
                label = mask_proxy(proxy) if proxy else "direct"
                print(f"Trying Kwork via {label}...")
                playwright, browser, page = await launch_browser(proxy=proxy)
                if args.refresh_session:
                    output = Path(settings.kwork_storage_state or "data/kwork_session.json")
                    session_refreshed = await refresh_session(page, output)
                    if session_refreshed:
                        print(f"Session saved: {output}")
                    else:
                        raise RuntimeError("Could not refresh Kwork session")

                project_ids = await fetch_offer_project_ids(page)
                print(f"Found {len(project_ids)} offers on Kwork")
                break
            except Exception as exc:
                last_error = exc
                print(f"Failed: {exc}")
            finally:
                if playwright and browser:
                    await close_browser(playwright, browser)

        if not project_ids and last_error:
            raise SystemExit(f"Could not fetch Kwork offers: {last_error}")

    if not project_ids:
        raise SystemExit("No project IDs to sync")

    results = await sync_project_ids(project_ids)
    synced = [item for item in results if item["status"] == "synced"]
    already = [item for item in results if item["status"] == "already_sent"]
    missing = [item for item in results if item["status"] not in ("synced", "already_sent")]

    print()
    print(f"Synced: {len(synced)}")
    for item in synced:
        print(f"  ✅ {item['project_id']} — {item['title']}")

    print(f"Already sent in DB: {len(already)}")
    for item in already:
        print(f"  · {item['project_id']} — {item['title']}")

    if missing:
        print(f"Skipped / not in DB: {len(missing)}")
        for item in missing:
            print(f"  ? {item['project_id']} — {item['status']} {item.get('title', '')}")

    if args.refresh_session and session_refreshed:
        print()
        print("Restart telegram-bot:")
        print("  docker compose restart telegram-bot")


if __name__ == "__main__":
    asyncio.run(main())
