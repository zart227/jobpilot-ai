"""Playwright helpers for Weblancer (Cloudflare + optional saved session)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.config import get_settings

logger = structlog.get_logger(__name__)

VERIFY_URL = "https://www.weblancer.net/freelance/veb-programmirovanie-31/"
HOME_URL = "https://www.weblancer.net/"
DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
_CF_TITLES = ("just a moment", "один момент", "checking your browser")


async def _goto_weblancer(page: Page, url: str, *, timeout_ms: int = 90_000) -> None:
    last_exc: Exception | None = None
    for wait_until in ("commit", "domcontentloaded"):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return
        except Exception as exc:
            last_exc = exc
            if "Timeout" not in str(exc):
                raise
    assert last_exc is not None
    raise last_exc


def is_weblancer_challenge_title(title: str) -> bool:
    return any(part in title.lower() for part in _CF_TITLES)


def _context_kwargs(settings) -> dict[str, Any]:
    context_kwargs: dict[str, Any] = {
        "viewport": DESKTOP_VIEWPORT,
        "locale": "ru-RU",
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    if settings.proxy_enabled and settings.proxy:
        from urllib.parse import urlparse

        parsed = urlparse(settings.proxy)
        proxy_config: dict[str, str] = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        }
        if parsed.username:
            proxy_config["username"] = parsed.username
        if parsed.password:
            proxy_config["password"] = parsed.password
        context_kwargs["proxy"] = proxy_config

    from app.services.weblancer_fetch import storage_state_path
    from app.services.weblancer_session import load_storage_state

    path = storage_state_path()
    if path:
        state = load_storage_state(path)
        if state:
            context_kwargs["storage_state"] = state
            logger.info("Weblancer browser using saved storage state", path=str(path))
    elif settings.weblancer_storage_state.strip():
        logger.warning(
            "Weblancer storage state path configured but file missing",
            path=settings.weblancer_storage_state.strip(),
        )
    return context_kwargs


async def launch_browser(
    *, headless: bool = True, channel: str | None = None
) -> tuple[Playwright, Browser, Page]:
    playwright = await async_playwright().start()
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "ignore_default_args": ["--enable-automation"],
    }
    if channel:
        launch_kwargs["channel"] = channel
    browser = None
    try:
        browser = await playwright.chromium.launch(**launch_kwargs)
        context: BrowserContext = await browser.new_context(**_context_kwargs(get_settings()))
        page = await context.new_page()
        return playwright, browser, page
    except Exception:
        if browser is not None:
            await browser.close()
        await playwright.stop()
        raise


async def fetch_listing_html_playwright(url: str, *, channel: str | None = None) -> str | None:
    playwright, browser, page = await launch_browser(headless=True, channel=channel)
    try:
        await _goto_weblancer(page, url)
        await page.wait_for_timeout(1200)
        if is_weblancer_challenge_title(await page.title()):
            logger.warning("Weblancer Playwright: Cloudflare challenge", url=url, channel=channel)
            return None
        return await page.content()
    except Exception as exc:
        logger.warning("Weblancer Playwright fetch failed", url=url, error=str(exc))
        return None
    finally:
        await close_browser(playwright, browser)


async def close_browser(playwright: Playwright | None, browser: Browser | None) -> None:
    if browser is not None:
        await browser.close()
    if playwright is not None:
        await playwright.stop()


async def page_has_listing(page: Page) -> bool:
    if is_weblancer_challenge_title(await page.title()):
        return False
    rows = page.locator("div.cols_table.container-fluid div.row")
    legacy = page.locator("table.items_list tr")
    if await rows.count() > 0:
        return True
    return await legacy.count() > 1


async def ensure_listing_accessible(page: Page) -> bool:
    try:
        await _goto_weblancer(page, VERIFY_URL)
        await page.wait_for_timeout(2000)
    except Exception as exc:
        logger.warning("Weblancer listing check navigation failed", error=str(exc))
        return False
    if is_weblancer_challenge_title(await page.title()):
        logger.warning(
            "Weblancer Cloudflare challenge — re-export session: "
            "python scripts/export_weblancer_session.py"
        )
        return False
    if not await page_has_listing(page):
        logger.warning("Weblancer listing empty or layout changed")
        return False
    return True
