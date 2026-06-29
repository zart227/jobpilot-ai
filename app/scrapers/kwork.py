import re
from datetime import datetime, timedelta
from typing import Any

import structlog

from app.config import get_settings
from app.schemas.job import ClientInfo, NormalizedJob
from app.scrapers.base import BaseScraper
from app.utils.formatting import normalize_ru_number

logger = structlog.get_logger(__name__)


def _kwork_project_number(external_id: str) -> int:
    match = re.search(r"(\d+)", external_id)
    return int(match.group(1)) if match else 0


class KworkScraper(BaseScraper):
    """Scraper for Kwork.ru freelance marketplace."""

    platform = "kwork"

    def __init__(self) -> None:
        settings = get_settings()
        self._email = settings.kwork_email
        self._password = settings.kwork_password
        self._category_url = settings.kwork_category_url
        self._max_pages = settings.kwork_max_pages

    async def scrape(self) -> list[NormalizedJob]:
        jobs: list[NormalizedJob] = []
        seen_ids: set[str] = set()
        playwright, browser, page = await self._launch_browser()

        try:
            settings = get_settings()
            if settings.kwork_scrape_login and self._email and self._password:
                try:
                    await self._login(page)
                except Exception as exc:
                    logger.warning("Kwork login skipped, using public listing", error=str(exc))

            for page_num in range(1, self._max_pages + 1):
                url = self._page_url(page_num)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_selector(".want-card", timeout=20000)
                except Exception:
                    logger.warning("Kwork page has no cards", page=page_num, url=url)
                    break
                await page.wait_for_timeout(1500)

                cards = page.locator(".want-card")
                count = await cards.count()
                logger.info("Kwork scraper found projects", page=page_num, count=count)
                if count == 0:
                    break

                for i in range(count):
                    card = cards.nth(i)
                    job = await self._extract_kwork_card(card, page)
                    if job and job.external_id not in seen_ids:
                        seen_ids.add(job.external_id)
                        jobs.append(job)
        except Exception as exc:
            logger.error("Kwork scraper failed", error=str(exc))
        finally:
            await self._close_browser(playwright, browser)

        # Kwork mixes old and new projects on the same page; higher ID ≈ newer project.
        jobs.sort(key=lambda job: _kwork_project_number(job.external_id), reverse=True)
        if jobs:
            logger.info(
                "Kwork scraper sorted by project id (newest first)",
                newest=_kwork_project_number(jobs[0].external_id),
                oldest=_kwork_project_number(jobs[-1].external_id),
            )

        logger.info("Kwork scraper total unique jobs", count=len(jobs))
        return jobs

    def _page_url(self, page_num: int) -> str:
        base = self._category_url
        if page_num <= 1:
            return base
        sep = "&" if "?" in base else "?"
        if "page=" in base:
            return re.sub(r"page=\d+", f"page={page_num}", base)
        return f"{base}{sep}page={page_num}"

    async def _login(self, page: Any) -> None:
        from app.services.kwork_browser import dismiss_overlays, login

        logged_in = await login(page, self._email, self._password)
        if not logged_in:
            raise RuntimeError("Kwork login failed")
        logger.info("Kwork login attempted")

    async def _extract_kwork_card(self, card: Any, page: Any) -> NormalizedJob | None:
        try:
            title_el = card.locator(".wants-card__header-title a, .wants-card__header-title").first
            title = (await title_el.text_content() or "").strip()
            if not title:
                return None

            link_el = card.locator(".wants-card__header-title a, a[href*='/projects/']").first
            href = await link_el.get_attribute("href")
            url = f"https://kwork.ru{href}" if href and href.startswith("/") else href

            description = await self._read_description(card, page, url)
            budget_min, budget_max, currency = await self._read_budget(card)

            skills: list[str] = []
            tag_els = card.locator(".tags__item, .skill-tag, .tag")
            for j in range(await tag_els.count()):
                tag = (await tag_els.nth(j).text_content() or "").strip()
                if tag:
                    skills.append(tag)

            client_el = card.locator('a[href*="/user/"]').first
            client_name = (await client_el.text_content() or "").strip() or None

            deadline_text = (await card.locator(".want-card__informers, .color-gray").first.text_content() or "")
            deadline = self._parse_kwork_deadline(deadline_text)

            external_id = self._project_external_id(url, title)

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
                raw_data={"source": "kwork.ru"},
            )
        except Exception as exc:
            logger.warning("Kwork card extraction failed", error=str(exc))
            return None

    def _project_external_id(self, url: str | None, title: str) -> str:
        if url:
            match = re.search(r"/projects/(\d+)", url)
            if match:
                return f"kwork-{match.group(1)}"
        return self._make_external_id(title, url or "")

    async def _read_description(self, card: Any, page: Any, url: str | None) -> str:
        expand = card.locator("span.kw-link-dashed:has-text('Показать полностью')").first
        if await expand.count() > 0:
            try:
                await expand.click()
                await page.wait_for_timeout(400)
            except Exception:
                pass

        desc_parts: list[str] = []
        for sel in (
            ".wants-card__description-text .breakwords",
            ".wants-card__description-text",
        ):
            els = card.locator(sel)
            for i in range(await els.count()):
                text = (await els.nth(i).text_content() or "").strip()
                if text and text not in desc_parts and "Показать полностью" not in text:
                    desc_parts.append(text)

        description = "\n".join(desc_parts).strip()
        if len(description) > 120:
            return description

        if url:
            detail_desc = await self._fetch_detail_description(page, url)
            if detail_desc:
                return detail_desc

        title = (await card.locator(".wants-card__header-title").first.text_content() or "").strip()
        return description or title

    async def _fetch_detail_description(self, page: Any, url: str) -> str:
        detail_page = await page.context.new_page()
        try:
            await detail_page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await detail_page.wait_for_timeout(1500)
            selectors = (
                ".want-card__description-text",
                ".wants-card__description-text",
                ".breakwords",
                ".project-description",
            )
            for sel in selectors:
                el = detail_page.locator(sel).first
                if await el.count() > 0:
                    text = (await el.text_content() or "").strip()
                    if len(text) > 80:
                        return text
        except Exception as exc:
            logger.debug("Kwork detail page fetch failed", url=url, error=str(exc))
        finally:
            await detail_page.close()
        return ""

    async def _read_budget(self, card: Any) -> tuple[float | None, float | None, str]:
        desired = (await card.locator(".wants-card__price").first.text_content() or "").strip()
        higher_el = card.locator(".wants-card__description-higher-price").first
        higher = ""
        if await higher_el.count() > 0:
            higher = (await higher_el.text_content(timeout=2000) or "").strip()
        combined = f"{desired} {higher}".strip()
        return self._parse_kwork_budget(combined)

    def _parse_kwork_budget(self, text: str) -> tuple[float | None, float | None, str]:
        if not text:
            return None, None, "RUB"

        text = re.sub(r"Осталось:\s*\d+\s*д\.?", "", text, flags=re.IGNORECASE)
        text = normalize_ru_number(text.replace("\xa0", " "))

        currency = "RUB" if ("₽" in text or "руб" in text.lower()) else "USD"

        rub_amounts = re.findall(
            r"([\d]+(?:[.,]\d+)?)\s*(?:₽|руб\.?)",
            text,
            flags=re.IGNORECASE,
        )
        if rub_amounts:
            numbers = [float(a.replace(",", ".")) for a in rub_amounts]
        else:
            numbers = [
                float(n.replace(",", "."))
                for n in re.findall(r"[\d]+(?:[.,]\d+)?", text)
                if float(n.replace(",", ".")) >= 100 or "до" in text.lower()
            ]

        numbers = [n for n in numbers if n >= 100 or (len(numbers) == 1 and n > 0)]
        if not numbers:
            return None, None, currency

        lower = text.lower()
        if "допустим" in lower and len(numbers) >= 2:
            # First amount = desired (желаемый), highest = allowable (допустимый)
            return numbers[0], max(numbers), currency
        if "до" in lower:
            # Only "до X" without separate allowable — X is desired budget
            return None, max(numbers), currency
        if len(numbers) >= 2:
            return min(numbers), max(numbers), currency
        return numbers[0], numbers[0], currency

    def _parse_kwork_deadline(self, text: str) -> datetime | None:
        if not text:
            return None
        match = re.search(r"Осталось:\s*(\d+)\s*д", text)
        if match:
            return datetime.now() + timedelta(days=int(match.group(1)))
        hours = re.search(r"Осталось:\s*(\d+)\s*ч", text)
        if hours:
            return datetime.now() + timedelta(hours=int(hours.group(1)))
        return None
