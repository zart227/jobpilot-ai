import json
from typing import Any

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
        raw = selectors or json.loads(settings.generic_scraper_selectors or "{}")
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

            budget_el = card.locator(self._selectors["budget"]).first
            budget_text = (await budget_el.text_content() or "").strip()
            budget_min, budget_max, currency = self._parse_budget(budget_text)

            skills: list[str] = []
            skill_els = card.locator(self._selectors["skills"])
            for j in range(await skill_els.count()):
                skill = (await skill_els.nth(j).text_content() or "").strip()
                if skill:
                    skills.append(skill)

            deadline_el = card.locator(self._selectors["deadline"]).first
            deadline_text = (await deadline_el.text_content() or "").strip()
            deadline = self._parse_deadline(deadline_text)

            client_el = card.locator(self._selectors["client"]).first
            client_name = (await client_el.text_content() or "").strip() or None

            link_el = card.locator(self._selectors["link"]).first
            href = await link_el.get_attribute("href") if await link_el.count() else None
            url = href if href and href.startswith("http") else self._url

            external_id = self._make_external_id(title, url or str(index))

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
