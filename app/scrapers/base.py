import hashlib
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import structlog
from playwright.async_api import Browser, Page, async_playwright

from app.schemas.job import ClientInfo, NormalizedJob

logger = structlog.get_logger(__name__)


class BaseScraper(ABC):
    platform: str = "generic"

    @abstractmethod
    async def scrape(self) -> list[NormalizedJob]:
        pass

    async def _launch_browser(self) -> tuple[Any, Browser, Page]:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        return playwright, browser, page

    async def _close_browser(self, playwright: Any, browser: Browser) -> None:
        await browser.close()
        await playwright.stop()

    def _make_external_id(self, *parts: str) -> str:
        raw = "|".join(parts)
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _parse_budget(self, text: str) -> tuple[float | None, float | None, str]:
        if not text:
            return None, None, "USD"
        currency = "USD"
        if "₽" in text or "руб" in text.lower():
            currency = "RUB"
        elif "€" in text:
            currency = "EUR"
        numbers = [float(n.replace(",", "").replace(" ", "")) for n in re.findall(r"[\d]+(?:[.,]\d+)?", text)]
        if not numbers:
            return None, None, currency
        if len(numbers) >= 2:
            return min(numbers[0], numbers[1]), max(numbers[0], numbers[1]), currency
        return numbers[0], numbers[0], currency

    def _parse_deadline(self, text: str) -> datetime | None:
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text.strip()[:10], fmt)
            except ValueError:
                continue
        return None
