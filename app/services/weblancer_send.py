"""Submit freelancer bids on Weblancer (Playwright + FlareSolverr cookies)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from playwright.async_api import Browser, Locator, Page, Playwright

from app.config import get_settings
from app.services.weblancer_browser import (
    _goto_weblancer,
    close_browser,
    is_weblancer_challenge_title,
    launch_browser,
)
from app.services.weblancer_fetch import fetch_flaresolverr_solution, flaresolverr_base_url
from app.utils.storage_state import save_browser_storage_state

logger = structlog.get_logger(__name__)

WEBLANCER_SESSION_ERROR = (
    "Сессия Weblancer недействительна (гость или вышли из аккаунта). "
    "Пересохраните: bash scripts/setup_weblancer_session.sh"
)
WEBLANCER_CLOUDFLARE_ERROR = (
    "Cloudflare блокирует Weblancer в Docker — поднимите FlareSolverr: "
    "docker compose --profile flaresolverr up -d flaresolverr"
)
WEBLANCER_ORDER_CLOSED_ERROR = (
    "Заказ на Weblancer уже закрыт — исполнитель выбран или статус «Завершён», заявку подать нельзя."
)
WEBLANCER_TARIFF_ERROR = (
    "На Weblancer не установлен тарифный план или не оплачена комиссия — заявку нельзя подать."
)

_CLOSED_BODY_MARKERS = (
    "статус",
    "завершен",
    "завершён",
    "выбранный исполнитель",
    "выбранные исполнители",
    "исполнитель уже выбран",
)
_OPEN_STATUS_MARKERS = ("открыт", "активен", "активный")
_TARIFF_BODY_MARKERS = (
    "тарифный план",
    "не установлен тариф",
    "не оплачена комиссия",
    "универсальных заявок",
)

_APPLY_LOCATORS = (
    'button:has-text("Подать заявку")',
    'button:has-text("Добавить заявку")',
    'button:has-text("Оставить заявку")',
    'a:has-text("Подать заявку")',
    'button:has-text("Предварительная заявка")',
    'button:has-text("Предварительная")',
    '[aria-label*="заявк" i]',
    'button:has-text("заявк")',
)

_SUBMIT_LOCATORS = (
    'button[type="submit"]:has-text("Отправ")',
    'button:has-text("Отправить заявку")',
    'button:has-text("Подать заявку")',
    'button:has-text("Сохранить")',
    'button:has-text("Отправить")',
)


def is_weblancer_order_closed_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    if lowered.strip() == WEBLANCER_ORDER_CLOSED_ERROR.lower():
        return True
    return any(
        phrase in lowered
        for phrase in (
            "заказ на weblancer уже закрыт",
            "заявку подать нельзя",
            "статус «завершён»",
        )
    )


def _playwright_cookie(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": raw["name"],
        "value": raw["value"],
        "domain": raw.get("domain", ".weblancer.net"),
        "path": raw.get("path", "/"),
        "secure": bool(raw.get("secure", True)),
        "httpOnly": bool(raw.get("httpOnly", False)),
        "sameSite": "Lax",
    }


def _cookies_indicate_guest(cookies: list[dict[str, Any]]) -> bool:
    for cookie in cookies:
        if cookie.get("name") == "stored_login" and str(cookie.get("value", "")).startswith("guest"):
            return True
    return False


async def _page_body_lower(page: Page) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=8_000)).lower()
    except Exception:
        return ""


async def _page_order_closed(page: Page) -> bool:
    body = await _page_body_lower(page)
    if any(marker in body for marker in ("выбранный исполнитель", "выбранные исполнители")):
        return True
    if "заверш" in body and "статус" in body:
        return True
    if "открыт" in body:
        return False
    return any(marker in body for marker in _CLOSED_BODY_MARKERS if marker not in _OPEN_STATUS_MARKERS)


async def _page_tariff_blocked(page: Page) -> bool:
    body = await _page_body_lower(page)
    return any(marker in body for marker in _TARIFF_BODY_MARKERS)


async def _page_requires_login(page: Page, *, solution_cookies: list[dict[str, Any]]) -> bool:
    if _cookies_indicate_guest(solution_cookies):
        return True
    try:
        if await page.get_by_role("button", name="Вход").count() > 0:
            return True
    except Exception:
        pass
    body = await _page_body_lower(page)
    head = "\n".join(body.split("\n")[:16])
    return "регистрация" in head and "вход" in head


async def _first_visible_locator(page: Page, selectors: tuple[str, ...]) -> Locator | None:
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                return loc
        except Exception:
            continue
    return None


async def send_weblancer_offer(job_url: str, content: str) -> tuple[bool, str | None, dict[str, Any]]:
    """Open project page and submit a preliminary bid comment."""
    steps: list[str] = []
    debug: dict[str, Any] = {
        "job_url": job_url,
        "content_length": len(content),
        "platform": "weblancer",
    }
    playwright: Playwright | None = None
    browser: Browser | None = None

    if not flaresolverr_base_url() and get_settings().jobpilot_in_docker:
        return (
            False,
            WEBLANCER_CLOUDFLARE_ERROR,
            {**debug, "steps": steps, "error_label": "cloudflare_blocked"},
        )

    try:
        solution = await fetch_flaresolverr_solution(job_url)
        if get_settings().jobpilot_in_docker and not solution:
            return (
                False,
                WEBLANCER_CLOUDFLARE_ERROR,
                {**debug, "steps": steps, "error_label": "cloudflare_blocked"},
            )

        playwright, browser, page = await launch_browser(headless=True)
        assert browser is not None
        context = page.context
        fs_cookies = list((solution or {}).get("cookies") or [])
        if fs_cookies:
            await context.add_cookies([_playwright_cookie(c) for c in fs_cookies])
            steps.append(f"primed cf/session cookies ({len(fs_cookies)})")
        elif flaresolverr_base_url():
            steps.append("flaresolverr returned no cookies; using storage state only")

        await _goto_weblancer(page, job_url)
        await page.wait_for_load_state("networkidle", timeout=120_000)
        await page.wait_for_timeout(1500)
        steps.append(f"открыт {job_url}")

        if is_weblancer_challenge_title(await page.title()):
            return (
                False,
                WEBLANCER_CLOUDFLARE_ERROR,
                {**debug, "steps": steps, "error_label": "cloudflare_blocked"},
            )

        if await _page_requires_login(page, solution_cookies=fs_cookies):
            return (
                False,
                WEBLANCER_SESSION_ERROR,
                {**debug, "steps": steps, "error_label": "login_failed"},
            )

        if await _page_order_closed(page):
            steps.append("заказ закрыт / исполнитель выбран")
            return (
                False,
                WEBLANCER_ORDER_CLOSED_ERROR,
                {**debug, "steps": steps, "error_label": "order_closed"},
            )

        heading = page.locator('h2:has-text("Заявки фрилансеров")')
        if await heading.count() > 0:
            await heading.scroll_into_view_if_needed()
            await page.wait_for_timeout(800)

        apply = await _first_visible_locator(page, _APPLY_LOCATORS)
        if not apply:
            if await _page_tariff_blocked(page):
                steps.append("нужен тарифный план / комиссия")
                return (
                    False,
                    WEBLANCER_TARIFF_ERROR,
                    {**debug, "steps": steps, "error_label": "weblancer_tariff_required"},
                )
            return (
                False,
                "Кнопка подачи заявки не найдена — возможно, изменился интерфейс Weblancer",
                {**debug, "steps": steps, "error_label": "apply_button_missing"},
            )

        await apply.click(timeout=30_000)
        await page.wait_for_timeout(1200)
        steps.append("нажата подача заявки")

        text_area = page.locator("textarea:visible").first
        if await text_area.count() == 0:
            text_area = page.locator('[contenteditable="true"]').first
        if await text_area.count() == 0:
            return (
                False,
                "Форма заявки Weblancer не открылась",
                {**debug, "steps": steps, "error_label": "offer_form_missing"},
            )

        tag = await text_area.evaluate("el => el.tagName.toLowerCase()")
        if tag == "textarea":
            await text_area.fill(content)
        else:
            await text_area.click()
            await text_area.fill(content)
        steps.append("заполнен текст заявки")

        submit = await _first_visible_locator(page, _SUBMIT_LOCATORS)
        if not submit:
            return (
                False,
                "Кнопка отправки заявки на Weblancer не найдена",
                {**debug, "steps": steps, "error_label": "submit_missing"},
            )

        await submit.click(timeout=30_000)
        await page.wait_for_timeout(2500)
        steps.append("нажата отправка заявки")

        if await _page_tariff_blocked(page):
            return (
                False,
                WEBLANCER_TARIFF_ERROR,
                {**debug, "steps": steps, "error_label": "weblancer_tariff_required"},
            )

        settings = get_settings()
        state_path = settings.weblancer_storage_state.strip()
        if state_path and Path(state_path).parent.exists():
            try:
                await save_browser_storage_state(page.context, Path(state_path))
            except Exception as exc:
                logger.debug("Weblancer session refresh after send failed", error=str(exc))

        debug["steps"] = steps
        debug["offer_action"] = "created"
        return True, None, debug
    except Exception as exc:
        logger.warning("Weblancer offer send failed", error=str(exc), url=job_url)
        return False, str(exc), {**debug, "steps": steps, "error_label": "send_failed"}
    finally:
        if playwright is not None and browser is not None:
            await close_browser(playwright, browser)
