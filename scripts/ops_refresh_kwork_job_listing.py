#!/usr/bin/env python3
"""Refresh one Kwork job from the category listing (updates listing_observed_at).

Falls back to the project /view page when the card is off the current listing pages.
Does not run FilterAgent, Telegram, or send. Requires --apply to write DB.
Use before reassess_kwork_job.py when assessment_gate is assessment_stale.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _client_info(client):
    from app.schemas.job import ClientInfo

    if not client or not client.external_id:
        return None
    metadata = dict(client.metadata_ or {})
    return ClientInfo(
        name=client.name,
        external_id=client.external_id,
        rating=float(client.rating) if client.rating is not None else None,
        reviews_count=client.reviews_count,
        metadata=metadata,
    )


async def _refresh_with_browser(scraper, job, client) -> tuple[object | None, str]:
    from app.config import get_settings
    from app.services.kwork_browser import close_browser, launch_browser
    from app.utils.proxy import get_proxy_candidates, mask_proxy

    settings = get_settings()
    candidates = list(get_proxy_candidates(settings, "openai"))
    if None not in candidates:
        candidates.insert(0, None)

    for proxy in candidates:
        mask_proxy(proxy) if proxy else "direct"
        playwright = browser = None
        try:
            playwright, browser, page = await launch_browser(proxy=proxy)
            if settings.kwork_scrape_login and scraper._email and scraper._password:
                try:
                    await scraper._login(page)
                except Exception:
                    pass
            normalized = await scraper.refresh_one_from_listing_page(page, job.external_id)
            source = "listing"
            if normalized is None:
                normalized = await scraper.normalized_from_project_view(
                    page,
                    url=job.url or f"https://kwork.ru/projects/{job.external_id.replace('kwork-', '')}",
                    external_id=job.external_id,
                    title=job.title,
                    description=job.description or "",
                    budget_min=float(job.budget_min) if job.budget_min is not None else None,
                    budget_max=float(job.budget_max) if job.budget_max is not None else None,
                    budget_currency=job.budget_currency or "RUB",
                    skills=list(job.skills or []),
                    deadline=job.deadline,
                    client=_client_info(client),
                )
                source = "project_view"
            return normalized, source
        except Exception:
            continue
        finally:
            if playwright is not None and browser is not None:
                await close_browser(playwright, browser)
    return None, "unavailable"


async def _run(job_id: uuid.UUID, *, apply: bool) -> dict:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Explicit DATABASE_URL is required")

    os.environ["DATABASE_URL"] = database_url
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db.models import Client, Job
    from app.db.session import AsyncSessionLocal
    from app.scrapers.kwork import KworkScraper
    from app.services.job_pipeline import JobPipelineService
    from app.services.opportunity_policy import kwork_job_assessment_rejection

    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job:
            return {"ok": False, "reason": "not_found"}
        if job.platform != "kwork":
            return {"ok": False, "reason": "not_kwork", "platform": job.platform}
        client = await session.get(Client, job.client_id) if job.client_id else None
        gate_before = await kwork_job_assessment_rejection(session, job) or "pass"
        external_id = job.external_id
        url = job.url

    scraper = KworkScraper()
    normalized, refresh_source = await _refresh_with_browser(scraper, job, client)
    if normalized is None:
        return {
            "ok": False,
            "reason": "not_on_listing_or_blocked",
            "job_id": str(job_id),
            "external_id": external_id,
            "url": url,
        }

    payload = {
        "ok": True,
        "job_id": str(job_id),
        "external_id": external_id,
        "refresh_source": refresh_source,
        "listing_observed_at": normalized.raw_data.get("listing_observed_at"),
        "assessment_gate_before": gate_before,
        "apply": apply,
    }
    if not apply:
        payload["dry_run"] = True
        return payload

    await JobPipelineService().save_normalized_job(normalized)
    async with AsyncSessionLocal() as session:
        job = await session.get(Job, job_id)
        gate_after = await kwork_job_assessment_rejection(session, job) or "pass"
        payload["assessment_gate_after"] = gate_after
        payload["listing_observed_at_saved"] = (job.raw_data or {}).get("listing_observed_at")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", type=uuid.UUID)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write refreshed listing fields to DATABASE_URL (default: dry-run JSON only)",
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args.job_id, apply=args.apply))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
