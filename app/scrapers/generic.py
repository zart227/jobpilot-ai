import hashlib
import json
from typing import Any
from urllib.parse import urljoin, urlparse

import structlog

from app.config import get_settings
from app.schemas.job import ClientInfo, NormalizedJob
from app.scrapers.base import BaseScraper

logger = structlog.get_logger(__name__)

DEFAULT_SELECTORS: dict[str, str] = {
    "job_card": "article.job, .job-card, .project-item, li.project",
    "title": "h2, h3, .title, .project-title",
    "description": ".description, .project-description, p.desc",
    "budget": ".budget, .price, .project-price",
    "skills": ".skills li, .tags span, .skill-tag",
    "deadline": ".deadline, .due-date, time",
    "client": ".client-name, .author, .buyer-name",
    "link": "a",
}


class GenericMarketplaceScraper(BaseScraper):
    """
    Configurable scraper for generic freelance marketplace HTML structure.
    CSS selectors are configured via GENERIC_SCRAPER_SELECTORS env variable.
    """

    platform = "generic"

    def __init__(self, url: str | None = None, selectors: dict[str, str] | None = None) -> None:
        settings = get_settings()
        self._url = url or settings.generic_scraper_url
        if selectors is not None:
            raw = selectors
        else:
            raw = json.loads(settings.generic_scraper_selectors or "{}")
        self._selectors = {**DEFAULT_SELECTORS, **raw}

    async def scrape(self) -> list[NormalizedJob]:
        jobs: list[NormalizedJob] = []
        playwright, browser, page = await self._launch_browser()

        try:
            await page.goto(self._url, wait_until="domcontentloaded", timeout=60000)
            cards = page.locator(self._selectors["job_card"])
            count = await cards.count()
            logger.info("Generic scraper found cards", count=count, url=self._url)

            for i in range(min(count, 50)):
                card = cards.nth(i)
                job = await self._extract_card(card, i)
                if job:
                    jobs.append(job)
        except Exception as exc:
            logger.error("Generic scraper failed", error=str(exc), url=self._url)
        finally:
            await self._close_browser(playwright, browser)

        return jobs

    async def _extract_card(self, card: Any, index: int) -> NormalizedJob | None:
        try:
            title_el = card.locator(self._selectors["title"]).first
            title = (await title_el.text_content() or "").strip()
            if not title:
                return None

            desc_el = card.locator(self._selectors["description"]).first
            description = (await desc_el.text_content() or title).strip()

            budget_min, budget_max, currency = None, None, "USD"
            budget_els = card.locator(self._selectors["budget"])
            if await budget_els.count() > 0:
                budget_text = (await budget_els.first.text_content() or "").strip()
                budget_min, budget_max, currency = self._parse_budget(budget_text)

            skills: list[str] = []
            skill_els = card.locator(self._selectors["skills"])
            for j in range(await skill_els.count()):
                skill = (await skill_els.nth(j).text_content() or "").strip()
                if skill:
                    skills.append(skill)

            deadline = None
            deadline_els = card.locator(self._selectors["deadline"])
            if await deadline_els.count() > 0:
                deadline_text = (await deadline_els.first.text_content() or "").strip()
                deadline = self._parse_deadline(deadline_text)

            client_name = None
            client_els = card.locator(self._selectors["client"])
            if await client_els.count() > 0:
                client_name = (await client_els.first.text_content() or "").strip() or None

            link_els = card.locator(self._selectors["link"])
            href = await link_els.first.get_attribute("href") if await link_els.count() else None
            url = urljoin(self._url, href) if href else self._url

            external_id = self._external_id_from_url(url) or self._make_external_id(title, url or str(index))

            return NormalizedJob(
                platform=self.platform,
                external_id=external_id,
                title=title,
                description=description,
                budget_min=budget_min,
                budget_max=budget_max,
                budget_currency=currency,
                skills=skills,
                deadline=deadline,
                url=url,
                client=ClientInfo(name=client_name, external_id=client_name),
                raw_data={"source_url": self._url, "index": index},
            )
        except Exception as exc:
            logger.warning("Failed to extract job card", index=index, error=str(exc))
            return None

    def _external_id_from_url(self, url: str | None) -> str | None:
        if not url:
            return None
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
        if parsed.query:
            normalized = f"{normalized}?{parsed.query}"
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]
