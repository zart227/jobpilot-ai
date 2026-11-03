#!/usr/bin/env python3
"""Check whether Kwork listing/project pages load from this runtime (no secrets)."""

from __future__ import annotations

import asyncio
import json
import sys

from app.config import get_settings
from app.services.kwork_browser import BLOCKED_TITLE, close_browser, launch_browser
from app.utils.proxy import get_proxy_candidates, mask_proxy


async def _probe_url(page, url: str) -> dict:
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    title = await page.title()
    return {
        "url": url,
        "title": title,
        "blocked": BLOCKED_TITLE in title,
        "want_cards": await page.locator(".want-card").count(),
        "propose_button": await page.get_by_text("Предложить услугу").count(),
    }


async def main() -> int:
    settings = get_settings()
    listing = settings.kwork_category_url
    sample_project = "https://kwork.ru/projects/3270495"
    candidates = list(get_proxy_candidates(settings, "openai")) or [None]

    report: list[dict] = []
    for proxy in candidates:
        label = mask_proxy(proxy) if proxy else "direct"
        playwright, browser, page = await launch_browser(proxy=proxy)
        try:
            listing_result = await _probe_url(page, listing)
            project_result = await _probe_url(page, sample_project)
            report.append(
                {
                    "proxy": label,
                    "listing": listing_result,
                    "sample_project": project_result,
                    "ok": not listing_result["blocked"] and listing_result["want_cards"] > 0,
                }
            )
        finally:
            await close_browser(playwright, browser)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    any_ok = any(row.get("ok") for row in report)
    if not any_ok:
        print(
            "\nKwork not reachable for scrape/send from this environment. "
            "Re-export data/kwork_session.json from a normal browser session or configure proxy.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
