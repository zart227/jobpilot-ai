"""Weblancer projects listing scraper (requires saved browser session — Cloudflare)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import structlog

from app.config import get_settings
from app.schemas.job import ClientInfo, NormalizedJob
from app.scrapers.base import BaseScraper
from app.scrapers.fl_ru import description_needs_detail_fetch, listing_page_url
from app.services.fl_ru_simple_filter import fl_ru_listing_reject_reason, fl_ru_title_budget_quick_reject
from app.services.weblancer_browser import (
    _goto_weblancer,
    close_browser,
    ensure_listing_accessible,
    is_weblancer_challenge_title,
    launch_browser,
)
from app.services.weblancer_fetch import fetch_listing_html, resolve_transport_chain
from app.services.weblancer_listing_parse import WeblancerListingRow, parse_listing_html
from app.utils.formatting import normalize_ru_number

logger = structlog.get_logger(__name__)

WEBLANCER_BASE = "https://www.weblancer.net"
_PROJECT_ID_RE = re.compile(r"-(\d+)/?$")


def weblancer_listing_urls_from_settings(settings) -> list[str]:
    urls = [part.strip() for part in settings.weblancer_listing_urls.split(",") if part.strip()]
    if urls:
        return urls
    return [settings.weblancer_listing_url.strip()]


def weblancer_project_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = _PROJECT_ID_RE.search(urlparse(url).path)
    return match.group(1) if match else None


class WeblancerScraper(BaseScraper):
    platform = "weblancer"

    def __init__(self) -> None:
        settings = get_settings()
        self._listing_urls = weblancer_listing_urls_from_settings(settings)
        self._max_pages = settings.weblancer_max_pages
        self._fetch_details = settings.weblancer_fetch_details
        self._simple_only = settings.weblancer_simple_orders_only
        self._transports = resolve_transport_chain(settings.weblancer_scrape_transport)
        cap = settings.weblancer_max_budget_rub
        self._max_budget_rub = cap if cap > 0 else None
        logger.info(
            "JobPilot AI Weblancer scraper initialized",
            listing_urls=self._listing_urls,
            max_pages=self._max_pages,
            fetch_details=self._fetch_details,
            simple_only=self._simple_only,
            transports=self._transports,
        )

    def _external_id(self, url: str | None, title: str) -> str:
        pid = weblancer_project_id_from_url(url)
        if pid:
            return f"weblancer-{pid}"
        return self._make_external_id(title, url or "")

    async def scrape(self) -> list[NormalizedJob]:
        for transport in self._transports:
            if transport == "flaresolverr" and not get_settings().weblancer_flaresolverr_url.strip():
                continue
            try:
                jobs = await self._scrape_transport(transport)
            except Exception as exc:
                logger.warning(
                    "Weblancer transport error",
                    transport=transport,
                    error=str(exc),
                )
                continue
            if jobs:
                logger.info(
                    "Weblancer scraper total unique jobs",
                    count=len(jobs),
                    transport=transport,
                )
                return jobs
            logger.warning("Weblancer transport produced no jobs", transport=transport)

        logger.warning("Weblancer scraper skipped: all transports failed (session/Cloudflare)")
        return []

    async def _scrape_transport(self, transport: str) -> list[NormalizedJob]:
        jobs: list[NormalizedJob] = []
        seen: set[str] = set()
        playwright, browser, page = None, None, None
        use_browser_details = transport in {"playwright", "chrome"}

        try:
            if use_browser_details:
                channel = "chrome" if transport == "chrome" else None
                playwright, browser, page = await launch_browser(headless=True, channel=channel)
                if not await ensure_listing_accessible(page):
                    return []

            for listing_url in self._listing_urls:
                for page_num in range(1, self._max_pages + 1):
                    url = listing_page_url(listing_url, page_num)
                    batch: list[NormalizedJob] = []

                    if use_browser_details and page is not None:
                        try:
                            await _goto_weblancer(page, url)
                            await page.wait_for_timeout(800)
                            if is_weblancer_challenge_title(await page.title()):
                                logger.warning("Weblancer listing blocked by Cloudflare", url=url)
                                break
                            await page.wait_for_selector(
                                "div.cols_table.container-fluid div.row, table.items_list",
                                timeout=25_000,
                            )
                            batch = await self._extract_listing_page(page)
                        except Exception as exc:
                            logger.warning(
                                "Weblancer listing page failed",
                                page=page_num,
                                url=url,
                                error=str(exc),
                                transport=transport,
                            )
                            break
                    else:
                        html = await fetch_listing_html(url, transport)  # type: ignore[arg-type]
                        if not html:
                            break
                        parsed = parse_listing_html(html)
                        if not parsed:
                            break
                        for index, row in enumerate(parsed):
                            job = self._job_from_listing_row(row, index)
                            if job:
                                batch.append(job)

                    logger.info(
                        "Weblancer scraper found projects",
                        page=page_num,
                        count=len(batch),
                        url=url,
                        transport=transport,
                    )

                    for job in batch:
                        if job.external_id in seen:
                            continue
                        if (
                            use_browser_details
                            and page is not None
                            and self._fetch_details
                            and description_needs_detail_fetch(job.description)
                        ):
                            job = await self._enrich_description(page, job)
                        if self._simple_only:
                            reason = fl_ru_listing_reject_reason(
                                title=job.title,
                                description=job.description,
                                budget_max=job.budget_max,
                                max_budget_rub=self._max_budget_rub,
                            )
                            if reason:
                                reason = reason.replace("FL.ru:", "Weblancer:")
                                logger.debug(
                                    "Weblancer skipped non-simple job",
                                    reason=reason,
                                    title=job.title,
                                )
                                continue
                        seen.add(job.external_id)
                        jobs.append(job)

                    if not batch:
                        break
        except Exception as exc:
            logger.error("Weblancer scraper failed", error=str(exc), transport=transport)
        finally:
            if playwright is not None or browser is not None:
                await close_browser(playwright, browser)

        jobs.sort(key=lambda j: int(weblancer_project_id_from_url(j.url) or "0"), reverse=True)
        return jobs

    def _job_from_listing_row(self, row: WeblancerListingRow, index: int) -> NormalizedJob | None:
        title = row.title.strip()
        if not title:
            return None
        if self._simple_only and fl_ru_title_budget_quick_reject(title, ""):
            return None
        url = self._absolute_url(row.href)
        if not url:
            return None
        budget_text = normalize_ru_number(row.budget_text)
        budget_min, budget_max, currency = self._parse_budget(budget_text)
        external_id = self._external_id(url, title)
        return NormalizedJob(
            platform=self.platform,
            external_id=external_id,
            title=title,
            description=row.description.strip() or title,
            budget_min=budget_min,
            budget_max=budget_max,
            budget_currency=currency,
            skills=[],
            deadline=None,
            url=url,
            client=ClientInfo(name=None, external_id=external_id),
            raw_data={
                "source": "weblancer.net",
                "listing_observed_at": datetime.now(UTC).isoformat(),
                "index": index,
            },
        )

    async def _extract_listing_page(self, page: Any) -> list[NormalizedJob]:
        jobs: list[NormalizedJob] = []
        rows = page.locator("div.cols_table.container-fluid div.row")
        row_count = await rows.count()
        if row_count > 0:
            for i in range(row_count):
                job = await self._extract_modern_row(rows.nth(i), i)
                if job:
                    jobs.append(job)
            return jobs

        table_rows = page.locator("table.items_list tr")
        tcount = await table_rows.count()
        for i in range(1, tcount):
            job = await self._extract_legacy_row(table_rows.nth(i), i)
            if job:
                jobs.append(job)
        return jobs

    async def _extract_modern_row(self, row: Any, index: int) -> NormalizedJob | None:
        try:
            title_el = row.locator("h2.title a, h2 a, .title a").first
            if await title_el.count() == 0:
                return None
            title = (await title_el.text_content() or "").strip()
            if not title:
                return None
            if self._simple_only and fl_ru_title_budget_quick_reject(title, ""):
                return None

            href = await title_el.get_attribute("href")
            url = self._absolute_url(href)
            if not url:
                return None

            description = title
            desc_el = row.locator("p, .description, .text-5").first
            if await desc_el.count() > 0:
                description = (await desc_el.text_content() or title).strip()

            budget_min, budget_max, currency = None, None, "RUB"
            price_el = row.locator("div.amount.title, .amount").first
            if await price_el.count() > 0:
                budget_text = normalize_ru_number((await price_el.text_content() or "").strip())
                budget_min, budget_max, currency = self._parse_budget(budget_text)

            external_id = self._external_id(url, title)
            return NormalizedJob(
                platform=self.platform,
                external_id=external_id,
                title=title,
                description=description,
                budget_min=budget_min,
                budget_max=budget_max,
                budget_currency=currency,
                skills=[],
                deadline=None,
                url=url,
                client=ClientInfo(name=None, external_id=external_id),
                raw_data={
                    "source": "weblancer.net",
                    "listing_observed_at": datetime.now(UTC).isoformat(),
                    "index": index,
                },
            )
        except Exception as exc:
            logger.warning("Weblancer row extraction failed", index=index, error=str(exc))
            return None

    async def _extract_legacy_row(self, row: Any, index: int) -> NormalizedJob | None:
        try:
            link = row.locator("td a").first
            if await link.count() == 0:
                return None
            title = (await link.text_content() or "").strip()
            if not title:
                return None
            href = await link.get_attribute("href")
            url = self._absolute_url(href)
            if not url:
                return None
            cols = row.locator("td")
            budget_text = ""
            if await cols.count() > 1:
                budget_text = (await cols.nth(1).text_content() or "").strip()
            budget_min, budget_max, currency = self._parse_budget(normalize_ru_number(budget_text))
            external_id = self._external_id(url, title)
            return NormalizedJob(
                platform=self.platform,
                external_id=external_id,
                title=title,
                description=title,
                budget_min=budget_min,
                budget_max=budget_max,
                budget_currency=currency,
                skills=[],
                deadline=None,
                url=url,
                client=ClientInfo(name=None, external_id=external_id),
                raw_data={
                    "source": "weblancer.net",
                    "listing_observed_at": datetime.now(UTC).isoformat(),
                    "index": index,
                    "layout": "legacy_table",
                },
            )
        except Exception as exc:
            logger.warning("Weblancer legacy row failed", index=index, error=str(exc))
            return None

    async def _enrich_description(self, page: Any, job: NormalizedJob) -> NormalizedJob:
        if not job.url:
            return job
        try:
            await _goto_weblancer(page, job.url)
            await page.wait_for_timeout(400)
            detail = page.locator(
                ".project-description, .job-description, article .description, .text-content"
            ).first
            if await detail.count() == 0:
                return job
            text = (await detail.inner_text() or "").strip()
            if len(text) <= len(job.description.strip()):
                return job
            job.description = text
            job.raw_data = {**(job.raw_data or {}), "description_source": "weblancer_project_page"}
            return job
        except Exception as exc:
            logger.debug("Weblancer detail fetch failed", url=job.url, error=str(exc))
            return job

    @staticmethod
    def _absolute_url(href: str | None) -> str | None:
        if not href:
            return None
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return urljoin(WEBLANCER_BASE, href)
