import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import structlog

from app.config import get_settings
from app.schemas.job import ClientInfo, NormalizedJob
from app.scrapers.base import BaseScraper
from app.utils.formatting import normalize_ru_number

logger = structlog.get_logger(__name__)


def _kwork_project_number(external_id: str) -> int:
    match = re.search(r"(\d+)", external_id)
    return int(match.group(1)) if match else 0


def _buyer_profile_path(href: str | None) -> str | None:
    if not href:
        return None
    parsed = urlsplit(href)
    if (parsed.scheme or parsed.netloc) and (
        parsed.scheme != 'https' or parsed.hostname != 'kwork.ru' or parsed.username or parsed.password
    ):
        return None
    if not re.fullmatch(r'/user/[A-Za-z0-9_.-]+/?', parsed.path):
        return None
    return parsed.path.rstrip('/')


class KworkScraper(BaseScraper):
    """Scraper for Kwork.ru freelance marketplace."""

    platform = "kwork"

    def __init__(self) -> None:
        settings = get_settings()
        self._email = settings.kwork_email
        self._password = settings.kwork_password
        self._category_url = settings.kwork_category_url
        self._max_pages = settings.kwork_max_pages
        self._buyer_cache: dict[str, dict] = {}

    async def _scrape_listing_pages(self, page: Any) -> list[NormalizedJob]:
        jobs: list[NormalizedJob] = []
        seen_ids: set[str] = set()
        for page_num in range(1, self._max_pages + 1):
            url = self._page_url(page_num)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                logger.warning("Kwork listing page failed", page=page_num, error=str(exc))
                break
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
        return jobs

    @staticmethod
    def _finalize_job_list(jobs: list[NormalizedJob]) -> list[NormalizedJob]:
        jobs.sort(key=lambda job: _kwork_project_number(job.external_id), reverse=True)
        if jobs:
            logger.info(
                "Kwork scraper sorted by project id (newest first)",
                newest=_kwork_project_number(jobs[0].external_id),
                oldest=_kwork_project_number(jobs[-1].external_id),
            )
        logger.info("Kwork scraper total unique jobs", count=len(jobs))
        return jobs

    async def scrape(self) -> list[NormalizedJob]:
        from app.services.kwork_browser import BLOCKED_TITLE, close_browser, launch_browser
        from app.services.kwork_pause import get_kwork_pause_reason, is_kwork_paused
        from app.utils.proxy import get_proxy_candidates, mask_proxy

        if is_kwork_paused():
            reason = get_kwork_pause_reason() or "Kwork приостановлен"
            logger.info("Kwork scraper skipped: connects limit pause", reason=reason)
            return []

        settings = get_settings()
        candidates = list(get_proxy_candidates(settings, "openai"))
        if not candidates:
            candidates = [None]

        saw_block = False
        for proxy in candidates:
            label = mask_proxy(proxy) if proxy else "direct"
            playwright = browser = None
            try:
                playwright, browser, page = await launch_browser(proxy=proxy)
                if settings.kwork_scrape_login and self._email and self._password:
                    try:
                        await self._login(page)
                    except Exception as exc:
                        logger.warning("Kwork login skipped, using public listing", error=str(exc))

                jobs = await self._scrape_listing_pages(page)
                if jobs:
                    logger.info("Kwork scraper listing ok", proxy=label, count=len(jobs))
                    return self._finalize_job_list(jobs)

                title = await page.title()
                if BLOCKED_TITLE in title:
                    saw_block = True
                    logger.warning(
                        "Kwork listing blocked (Доступ заблокирован)",
                        proxy=label,
                        hint="Re-export kwork_session.json from a normal browser or add OPENAI_PROXY_URL",
                    )
            except Exception as exc:
                logger.error("Kwork scraper failed", proxy=label, error=str(exc))
            finally:
                if playwright is not None and browser is not None:
                    await close_browser(playwright, browser)

        if saw_block:
            logger.error("Kwork scraper: no route could load listing (IP/session block)")
        return self._finalize_job_list([])

    async def refresh_one_from_listing(self, target_external_id: str) -> NormalizedJob | None:
        """Find one project on the public listing and return a fresh NormalizedJob snapshot."""
        from app.services.kwork_browser import BLOCKED_TITLE, close_browser, launch_browser
        from app.services.kwork_pause import get_kwork_pause_reason, is_kwork_paused
        from app.utils.proxy import get_proxy_candidates, mask_proxy

        if is_kwork_paused():
            logger.info("Kwork refresh skipped: pause", reason=get_kwork_pause_reason())
            return None

        settings = get_settings()
        candidates = list(get_proxy_candidates(settings, "openai"))
        if None not in candidates:
            candidates.insert(0, None)

        target = target_external_id.strip()
        for proxy in candidates:
            label = mask_proxy(proxy) if proxy else "direct"
            playwright = browser = None
            try:
                playwright, browser, page = await launch_browser(proxy=proxy)
                if settings.kwork_scrape_login and self._email and self._password:
                    try:
                        await self._login(page)
                    except Exception as exc:
                        logger.warning("Kwork login skipped for refresh", error=str(exc))
                job = await self.refresh_one_from_listing_page(page, target)
                if job:
                    logger.info(
                        "Kwork listing refresh matched project",
                        external_id=target,
                        proxy=label,
                    )
                    return job
                title = await page.title()
                if BLOCKED_TITLE in title:
                    logger.warning("Kwork listing blocked during refresh", proxy=label)
            except Exception as exc:
                logger.error("Kwork refresh failed", proxy=label, error=str(exc))
            finally:
                if playwright is not None and browser is not None:
                    await close_browser(playwright, browser)
        return None

    async def refresh_one_from_listing_page(self, page: Any, target_external_id: str) -> NormalizedJob | None:
        target = target_external_id.strip()
        for page_num in range(1, self._max_pages + 1):
            url = self._page_url(page_num)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                break
            try:
                await page.wait_for_selector(".want-card", timeout=20000)
            except Exception:
                break
            await page.wait_for_timeout(1500)
            cards = page.locator(".want-card")
            count = await cards.count()
            if count == 0:
                break
            for i in range(count):
                job = await self._extract_kwork_card(cards.nth(i), page)
                if job and job.external_id == target:
                    return job
        return None

    async def normalized_from_project_view(
        self,
        page: Any,
        *,
        url: str,
        external_id: str,
        title: str,
        description: str,
        budget_min: float | None,
        budget_max: float | None,
        budget_currency: str,
        skills: list[str],
        deadline: datetime | None,
        client: ClientInfo | None,
    ) -> NormalizedJob:
        """Refresh listing_observed_at (and description when readable) from the project page."""
        view_url = url.rstrip("/")
        if not view_url.endswith("/view"):
            view_url = f"{view_url}/view"
        await page.goto(view_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
        fresh_desc = await self._fetch_detail_description(page, view_url) or description
        return NormalizedJob(
            platform=self.platform,
            external_id=external_id,
            title=title,
            description=fresh_desc,
            budget_min=budget_min,
            budget_max=budget_max,
            budget_currency=budget_currency,
            skills=skills,
            deadline=deadline,
            url=url,
            client=client,
            raw_data={"source": "kwork.ru", "listing_observed_at": datetime.now(UTC).isoformat()},
        )

    def _page_url(self, page_num: int) -> str:
        base = self._category_url
        if page_num <= 1:
            return base
        sep = "&" if "?" in base else "?"
        if "page=" in base:
            return re.sub(r"page=\d+", f"page={page_num}", base)
        return f"{base}{sep}page={page_num}"

    async def _login(self, page: Any) -> None:
        from app.services.kwork_browser import login

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

            client_name, client_href, card_stats = await self._read_card_buyer(card)
            history = await self._read_buyer_history(page, client_href) if client_href else {}
            history = {**history, **card_stats}

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
                client=ClientInfo(name=client_name, external_id=client_href or client_name, rating=history.get("rating"), metadata=history),
                raw_data={"source": "kwork.ru", "listing_observed_at": datetime.now(UTC).isoformat()},
            )
        except Exception as exc:
            logger.warning("Kwork card extraction failed", error=str(exc))
            return None

    async def _read_card_buyer(self, card: Any) -> tuple[str | None, str | None, dict]:
        """Read one buyer block; never use job description or unrelated links as evidence."""
        from app.services.opportunity_policy import parse_kwork_card_buyer_stats

        blocks = card.locator(':scope > .want-payer-statistic, :scope > .want-card__statistic')
        if await blocks.count() != 1:
            return None, None, {}
        block = blocks.first
        links = block.locator('.want-card__statistic-item a[href*="/user/"]')
        if await links.count() != 1:
            return None, None, {}
        link = links.first
        href = _buyer_profile_path(await link.get_attribute('href'))
        if not href:
            return None, None, {}
        name = (await link.text_content() or '').strip() or None
        rows = block.locator('.want-card__statistic-item')
        values = [await rows.nth(i).inner_text() for i in range(await rows.count())]
        if sum('Покупатель:' in row for row in values) != 1:
            return None, None, {}
        if any(sum(label in row for row in values) != 1 for label in (
            'Размещено проектов на бирже:', 'Нанято:',
        )):
            return name, href, {}
        stats = parse_kwork_card_buyer_stats('\n'.join(values))
        if 'projects_posted' in stats and 'hire_percent' in stats:
            stats['stats_source'] = 'kwork_buyer_block_v1'
        return name, href, stats

    async def _read_buyer_history(self, page: Any, href: str) -> dict:
        from app.services.opportunity_policy import parse_buyer_history

        if href in self._buyer_cache:
            return self._buyer_cache[href]
        detail = await page.context.new_page()
        result = {}
        try:
            url = f"https://kwork.ru{href}"
            await detail.goto(url, wait_until="domcontentloaded", timeout=45000)
            result = parse_buyer_history(await detail.locator("body").inner_text())
            result["profile_url"] = url
            result["checked_at"] = datetime.now().isoformat()
        except Exception as exc:
            logger.warning("Buyer history unavailable", error=str(exc))
        finally:
            await detail.close()
        self._buyer_cache[href] = result
        return result

    def _project_external_id(self, url: str | None, title: str) -> str:
        if url:
            match = re.search(r"/projects/(\d+)", url)
            if match:
                return f"kwork-{match.group(1)}"
        return self._make_external_id(title, url or "")

    async def _read_description(self, card: Any, page: Any, url: str | None) -> str:
        expand = card.locator("span.kw-link-dashed:has-text('Показать полностью')").first
        expansion_unconfirmed = False
        if await expand.count() > 0:
            expansion_unconfirmed = True
            try:
                await expand.click()
                await page.wait_for_timeout(400)
                expansion_unconfirmed = await expand.is_visible()
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
        looks_truncated = description.endswith(("...", "…"))
        if len(description) > 120 and not expansion_unconfirmed and not looks_truncated:
            return description

        if url:
            detail_desc = await self._fetch_detail_description(page, url)
            if detail_desc and not detail_desc.rstrip().endswith(("...", "…")):
                return detail_desc

        if expansion_unconfirmed or looks_truncated:
            logger.info("Kwork description may be truncated", url=url)
            return ""

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
