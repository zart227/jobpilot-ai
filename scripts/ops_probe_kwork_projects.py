#!/usr/bin/env python3
"""Probe Kwork project pages for live-send readiness (read-only, no DB writes)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re

from app.config import get_settings
from app.services.kwork_browser import BLOCKED_TITLE, close_browser, launch_browser
from app.utils.proxy import get_proxy_candidates, mask_proxy


def _project_url(project_id: str) -> str:
    digits = re.sub(r"\D", "", project_id)
    if not digits:
        raise ValueError(f"invalid project id: {project_id!r}")
    return f"https://kwork.ru/projects/{digits}"


async def _probe_page(page, url: str) -> dict:
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    final_url = page.url
    title = await page.title()
    project_id = url.rstrip("/").split("/")[-1]
    final_path = final_url.split("?")[0].rstrip("/")
    on_project = final_path.endswith(f"/projects/{project_id}") or f"/projects/{project_id}/" in final_path
    propose = await page.get_by_text("Предложить услугу").count()
    edit_offer = await page.get_by_text("Редактировать предложение").count()
    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "blocked": BLOCKED_TITLE in title,
        "on_project_page": on_project,
        "propose_button_count": propose,
        "edit_offer_button_count": edit_offer,
        "new_offer_likely": on_project and propose > 0 and edit_offer == 0,
    }


async def main_async(project_ids: list[str]) -> int:
    settings = get_settings()
    candidates = list(get_proxy_candidates(settings, "openai"))
    if None not in candidates:
        candidates.insert(0, None)

    urls = [_project_url(pid) for pid in project_ids]
    report: list[dict] = []
    for proxy in candidates:
        label = mask_proxy(proxy) if proxy else "direct"
        playwright, browser, page = await launch_browser(proxy=proxy)
        try:
            for url in urls:
                row = await _probe_page(page, url)
                row["proxy"] = label
                report.append(row)
            break
        finally:
            await close_browser(playwright, browser)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    any_blocked = any(r.get("blocked") for r in report)
    any(r.get("new_offer_likely") for r in report)
    if any_blocked:
        return 1
    return 0 if report else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project_ids",
        nargs="+",
        help="Kwork project numeric ids (e.g. 3271087)",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args.project_ids))


if __name__ == "__main__":
    raise SystemExit(main())
