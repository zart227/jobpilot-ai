"""FL.ru projects listing scraper (public feed; no login required for scrape)."""

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import structlog

from app.config import get_settings
from app.schemas.job import ClientInfo, NormalizedJob
from app.scrapers.base import BaseScraper
from app.services.fl_ru_browser import _goto_fl_ru
from app.services.fl_ru_simple_filter import (
    fl_ru_listing_html_is_free_for_all,
    fl_ru_listing_reject_reason,
    fl_ru_title_budget_quick_reject,
)
from app.utils.formatting import normalize_ru_number

logger = structlog.get_logger(__name__)

FL_BASE = "https://www.fl.ru"


def _fl_project_number(external_id: str) -> int:
    match = re.search(r"(\d+)", external_id)
    return int(match.group(1)) if match else 0


def fl_ru_listing_urls_from_settings(settings) -> list[str]:
    urls = [part.strip() for part in settings.fl_ru_listing_urls.split(",") if part.strip()]
    if urls:
        return urls
    return [settings.fl_ru_listing_url.strip()]


def listing_page_url(base_url: str, page_num: int) -> str:
    if page_num <= 1:
        return base_url
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["page"] = [str(page_num)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def description_needs_detail_fetch(description: str) -> bool:
    text = description.strip()
    if not text:
        return True
    if text.endswith("...") or text.endswith("…"):
        return True
    return len(text) < 120


class FlRuScraper(BaseScraper):
    platform = "fl_ru"

    def __init__(self) -> None:
        settings = get_settings()
        self._listing_urls = fl_ru_listing_urls_from_settings(settings)
        self._max_pages = settings.fl_ru_max_pages
        self._fetch_details = settings.fl_ru_fetch_details
        self._simple_only = settings.fl_ru_simple_orders_only
        self._free_offers_only = settings.fl_ru_free_offers_only
        cap = settings.fl_ru_max_budget_rub
        self._max_budget_rub = cap if cap > 0 else None
        logger.info(
            "JobPilot AI FL.ru scraper initialized",
            listing_urls=self._listing_urls,
            max_pages=self._max_pages,
            fetch_details=self._fetch_details,
            free_offers_only=self._free_offers_only,
        )

    def _project_external_id(self, url: str | None, title: str, fallback_id: str) -> str:
        if url:
            match = re.search(r"/projects/(\d+)", url)
            if match:
                return f"fl_ru-{match.group(1)}"
        if fallback_id:
            match = re.search(r"(\d+)", fallback_id)
            if match:
                return f"fl_ru-{match.group(1)}"
        return self._make_external_id(title, url or fallback_id)

    async def scrape(self) -> list[NormalizedJob]:
        from app.services.fl_ru_browser import close_browser, ensure_authenticated, launch_browser

        jobs: list[NormalizedJob] = []
        seen_ids: set[str] = set()
        playwright, browser, page = await launch_browser()

        try:
            if not await ensure_authenticated(page):
                logger.warning("FL.ru scraper skipped: authentication failed or session expired")
                return []

            for listing_url in self._listing_urls:
                for page_num in range(1, self._max_pages + 1):
                    url = listing_page_url(listing_url, page_num)
                    try:
                        await _goto_fl_ru(page, url)
                        await page.wait_for_selector("div.b-post[qa-project-name]", timeout=25000)
                    except Exception as exc:
                        logger.warning(
                            "FL.ru listing page failed",
                            page=page_num,
                            url=url,
                            error=str(exc),
                        )
                        break

                    cards = page.locator("div.b-post[qa-project-name]")
                    count = await cards.count()
                    logger.info(
                        "FL.ru scraper found projects",
                        page=page_num,
                        count=count,
                        url=url,
                    )
                    if count == 0:
                        break

                    batch: list[NormalizedJob] = []
                    for i in range(count):
                        card = cards.nth(i)
                        job = await self._extract_card(card, page, i)
                        if job and job.external_id not in seen_ids:
                            batch.append(job)

                    for job in batch:
                        if self._fetch_details and description_needs_detail_fetch(job.description):
                            job = await self._enrich_description(page, job)
                        if self._simple_only:
                            reason = fl_ru_listing_reject_reason(
                                title=job.title,
                                description=job.description,
                                budget_max=job.budget_max,
                                max_budget_rub=self._max_budget_rub,
                            )
                            if reason:
                                logger.debug(
                                    "FL.ru skipped non-simple job",
                                    reason=reason,
                                    title=job.title,
                                )
                                continue
                        seen_ids.add(job.external_id)
                        jobs.append(job)
        except Exception as exc:
            logger.error("FL.ru scraper failed", error=str(exc), listing_urls=self._listing_urls)
        finally:
            await close_browser(playwright, browser)

        jobs.sort(key=lambda job: _fl_project_number(job.external_id), reverse=True)
        logger.info("FL.ru scraper total unique jobs", count=len(jobs))
        return jobs

    async def _enrich_description(self, page: Any, job: NormalizedJob) -> NormalizedJob:
        if not job.url:
            return job
        try:
            await _goto_fl_ru(page, job.url)
            await page.wait_for_timeout(400)
            detail = page.locator(".fl-project-content__description-text").first
            if await detail.count() == 0:
                return job
            text = (await detail.inner_text() or "").strip()
            if len(text) <= len(job.description.strip()):
                return job
            job.description = text
            job.raw_data = {**(job.raw_data or {}), "description_source": "fl_ru_project_page"}
            return job
        except Exception as exc:
            logger.debug("FL.ru detail fetch failed", url=job.url, error=str(exc))
            return job

    async def _extract_card(self, card: Any, page: Any, index: int) -> NormalizedJob | None:
        try:
            qa_name = await card.get_attribute("qa-project-name")
            project_dom_id = await card.get_attribute("id")

            title_el = card.locator("h2.b-post__title a").first
            title = (await title_el.text_content() or "").strip()
            if not title:
                return None
            if self._free_offers_only:
                card_html = await card.inner_html()
                if not fl_ru_listing_html_is_free_for_all(card_html):
                    logger.debug(
                        "FL.ru skipped job without free «Для всех» badge",
                        title=title,
                    )
                    return None
            if self._simple_only:
                quick = fl_ru_title_budget_quick_reject(title, "")
                if quick:
                    return None

            href = await title_el.get_attribute("href")
            url = self._absolute_url(href)
            if not url:
                return None

            description_el = card.locator(".b-post__grid_descript .b-post__txt").first
            description = (await description_el.text_content() or title).strip()

            budget_min, budget_max, currency = None, None, "RUB"
            budget_el = card.locator(".b-post__price").first
            if await budget_el.count() > 0:
                budget_text = normalize_ru_number((await budget_el.inner_text() or "").strip())
                budget_min, budget_max, currency = self._parse_budget(budget_text)

            posted_ago: str | None = None
            foot = card.locator(".b-post__foot").first
            if await foot.count() > 0:
                foot_text = (await foot.inner_text() or "").strip()
                if foot_text:
                    posted_ago = foot_text.split("\n")[0][:120] or None

            external_id = self._project_external_id(
                url,
                title,
                (qa_name or project_dom_id or "").replace("project-item", ""),
            )

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
                    "source": "fl.ru",
                    "listing_observed_at": datetime.now(UTC).isoformat(),
                    "posted_ago": posted_ago,
                    "index": index,
                },
            )
        except Exception as exc:
            logger.warning("FL.ru card extraction failed", index=index, error=str(exc))
            return None

    @staticmethod
    def _absolute_url(href: str | None) -> str | None:
        if not href:
            return None
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return urljoin(FL_BASE, href)
