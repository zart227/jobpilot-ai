"""Playwright helpers for FL.ru (session export, optional login)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.config import get_settings
from app.utils.storage_state import save_browser_storage_state

logger = structlog.get_logger(__name__)

LOGIN_URL = "https://www.fl.ru/account/login/"
VERIFY_URL = "https://www.fl.ru/projects/"
DESKTOP_VIEWPORT = {"width": 1280, "height": 900}


def is_fl_login_url(url: str) -> bool:
    return "/account/login" in url


async def read_current_uid(page: Page) -> int:
    raw = await page.evaluate(
        """() => {
            const el = document.querySelector('meta[name="current-uid"]');
            return el ? el.getAttribute('content') : '0';
        }"""
    )
    try:
        return int(str(raw or "0"))
    except ValueError:
        return 0


async def _goto_fl_ru(page: Page, url: str, *, timeout_ms: int = 90_000) -> None:
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


async def session_is_active(page: Page) -> bool:
    try:
        await _goto_fl_ru(page, VERIFY_URL)
        await page.wait_for_timeout(1000)
    except Exception as exc:
        logger.warning("FL.ru session check navigation failed", error=str(exc))
        return False
    if is_fl_login_url(page.url):
        return False
    return await read_current_uid(page) > 0


async def launch_browser(*, headless: bool = True) -> tuple[Playwright, Browser, Page]:
    settings = get_settings()
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=headless)
    try:
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

        storage_state = settings.fl_ru_storage_state.strip()
        if storage_state and Path(storage_state).is_file():
            context_kwargs["storage_state"] = storage_state
            logger.info("FL.ru browser using saved storage state", path=storage_state)
        elif storage_state:
            logger.warning(
                "FL.ru storage state path configured but file missing",
                path=storage_state,
            )

        context: BrowserContext = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        return playwright, browser, page
    except BaseException:
        await browser.close()
        await playwright.stop()
        raise


async def close_browser(playwright: Playwright, browser: Browser) -> None:
    await browser.close()
    await playwright.stop()


async def login(page: Page, email: str, password: str) -> bool:
    settings = get_settings()
    storage_state = settings.fl_ru_storage_state.strip()
    if storage_state and Path(storage_state).is_file():
        if await session_is_active(page):
            logger.info("FL.ru session active via storage state")
            return True
        logger.error(
            "FL.ru saved session expired — re-run scripts/export_fl_ru_session.py",
            path=storage_state,
        )
        return False

    if await session_is_active(page):
        logger.info("FL.ru session already active")
        return True

    if not email or not password:
        logger.error("FL.ru login failed: FL_RU_EMAIL/FL_RU_PASSWORD or FL_RU_STORAGE_STATE required")
        return False

    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1500)
    try:
        await page.wait_for_selector('input[name="username"]', state="visible", timeout=20000)
    except Exception:
        if is_fl_login_url(page.url):
            logger.error("FL.ru login form not found (captcha or layout change)")
            return False
        return await session_is_active(page)

    await page.fill('input[name="username"]', email)
    await page.fill('input[name="password"]', password)
    await page.click("#submit-button, button[type='submit']")

    try:
        await page.wait_for_function(
            """() => {
                const uid = document.querySelector('meta[name="current-uid"]');
                return uid && parseInt(uid.getAttribute('content') || '0', 10) > 0;
            }""",
            timeout=45000,
        )
    except Exception:
        if is_fl_login_url(page.url):
            logger.error("FL.ru login failed: still on login page")
            return False
        if not await session_is_active(page):
            logger.error("FL.ru login failed: user id not detected after submit")
            return False

    if storage_state:
        output = Path(storage_state)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            await save_browser_storage_state(page.context, output)
            logger.info("FL.ru session saved", path=str(output))
        except Exception as exc:
            logger.warning("FL.ru session save failed", error=str(exc))

    return True


async def ensure_authenticated(page: Page) -> bool:
    """Require auth only when a session file exists or FL_RU_SCRAPE_LOGIN is enabled."""
    settings = get_settings()
    state_path = settings.fl_ru_storage_state.strip()
    has_state_file = bool(state_path and Path(state_path).is_file())
    has_creds = bool(settings.fl_ru_email.strip() and settings.fl_ru_password.strip())
    wants_login = settings.fl_ru_scrape_login and has_creds

    if not has_state_file and not wants_login:
        return True

    if await session_is_active(page):
        return True

    if has_state_file:
        logger.warning(
            "FL.ru session UID check failed; continuing with saved storage state",
            path=state_path,
        )
        if wants_login:
            return await login(page, settings.fl_ru_email, settings.fl_ru_password)
        return True

    if wants_login:
        return await login(page, settings.fl_ru_email, settings.fl_ru_password)

    return False


FL_RU_PAID_OFFERS_ERROR = (
    "На FL.ru для этого заказа нужен платный отклик (пакет на fl.ru/payed). "
    "Либо ищите заказы с пометкой «для всех» — на них отклик бесплатный."
)
FL_RU_SESSION_ERROR = (
    "Сессия FL.ru недействительна — пересохраните: python scripts/export_fl_ru_session.py"
)
FL_RU_ORDER_CLOSED_ERROR = (
    "Заказ на FL.ru уже закрыт — исполнитель выбран или проект в архиве, отклики не принимаются."
)

_FL_RU_CLOSED_BODY_MARKERS = (
    "заказчик выбрал исполнителя",
    "исполнитель уже выбран",
    "проект закрыт",
    "работа над проектом завершена",
    "отклики не принимаются",
    "проект больше не принимает",
    "проект находится в архиве",
)
_FL_RU_PAID_OFFER_BODY_MARKERS = (
    "купить 1 отклик",
    "отклики только пакетами",
    "купить пакет откликов",
)


def is_fl_ru_order_closed_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    if lowered.strip() == FL_RU_ORDER_CLOSED_ERROR.lower():
        return True
    return any(
        phrase in lowered
        for phrase in (
            "заказ на fl.ru уже закрыт",
            "отклики не принимаются",
            "проект закрыт",
            "заказчик выбрал исполнителя",
        )
    )


async def _fl_ru_page_body_lower(page: Page) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=8_000)).lower()
    except Exception:
        return ""


async def _fl_ru_project_page_closed(page: Page) -> bool:
    body = await _fl_ru_page_body_lower(page)
    return any(marker in body for marker in _FL_RU_CLOSED_BODY_MARKERS)


async def _fl_ru_project_needs_paid_offer(page: Page) -> bool:
    body = await _fl_ru_page_body_lower(page)
    return any(marker in body for marker in _FL_RU_PAID_OFFER_BODY_MARKERS)


async def send_fl_ru_offer(job_url: str, content: str) -> tuple[bool, str | None, dict]:
    """Submit a project response on FL.ru when the account can respond."""
    steps: list[str] = []
    debug: dict = {"job_url": job_url, "content_length": len(content), "platform": "fl_ru"}
    playwright = browser = None
    try:
        playwright, browser, page = await launch_browser()
        if not await ensure_authenticated(page):
            return False, FL_RU_SESSION_ERROR, {**debug, "steps": steps, "error_label": "login_failed"}

        await _goto_fl_ru(page, job_url)
        steps.append(f"открыт проект {job_url}")

        reply = page.locator('#reply_offer, a.ui-button._success:has-text("Откликнуться")').first
        if await reply.count() == 0:
            if await _fl_ru_project_page_closed(page):
                steps.append("заказчик выбрал исполнителя / проект закрыт")
                return (
                    False,
                    FL_RU_ORDER_CLOSED_ERROR,
                    {**debug, "steps": steps, "error_label": "order_closed"},
                )
            if await _fl_ru_project_needs_paid_offer(page):
                steps.append("нужен платный отклик (Купить 1 отклик / пакет)")
                return (
                    False,
                    FL_RU_PAID_OFFERS_ERROR,
                    {**debug, "steps": steps, "error_label": "fl_ru_paid_offers_required"},
                )
            return (
                False,
                "Кнопка «Откликнуться» не найдена — возможно, изменился интерфейс FL.ru",
                {**debug, "steps": steps, "error_label": "reply_button_missing"},
            )

        href = (await reply.get_attribute("href") or "").strip()
        if "payed" in href.lower():
            steps.append("требуется платный пакет откликов (ссылка на /payed/)")
            return (
                False,
                FL_RU_PAID_OFFERS_ERROR,
                {**debug, "steps": steps, "error_label": "fl_ru_paid_offers_required"},
            )

        await reply.click(timeout=30_000)
        await page.wait_for_timeout(1500)
        steps.append("нажата «Откликнуться»")

        if "payed" in page.url.lower():
            steps.append(f"редирект на оплату: {page.url}")
            return (
                False,
                FL_RU_PAID_OFFERS_ERROR,
                {**debug, "steps": steps, "error_label": "fl_ru_paid_offers_required"},
            )

        text_areas = page.locator("textarea")
        if await text_areas.count() == 0:
            return (
                False,
                "Форма отклика FL.ru не открылась (возможен новый интерфейс или капча)",
                {**debug, "steps": steps, "error_label": "offer_form_missing"},
            )

        filled = False
        for index in range(await text_areas.count()):
            area = text_areas.nth(index)
            if not await area.is_visible():
                continue
            await area.fill(content)
            filled = True
            name = await area.get_attribute("name")
            steps.append(f"заполнено поле textarea{name or index}")
            break

        if not filled:
            return (
                False,
                "Не найдено видимое поле для текста отклика на FL.ru",
                {**debug, "steps": steps, "error_label": "offer_textarea_missing"},
            )

        submit = page.locator(
            'button[type="submit"]:has-text("Отправ"), '
            'input[type="submit"], '
            'button:has-text("Откликнуться")'
        ).first
        if await submit.count() == 0:
            return (
                False,
                "Кнопка отправки отклика на FL.ru не найдена",
                {**debug, "steps": steps, "error_label": "submit_missing"},
            )

        await submit.click(timeout=30_000)
        await page.wait_for_timeout(2500)
        steps.append("нажата отправка отклика")

        settings = get_settings()
        if settings.fl_ru_storage_state.strip():
            try:
                path = Path(settings.fl_ru_storage_state)
                await save_browser_storage_state(page.context, path)
            except Exception as exc:
                logger.debug("FL.ru session refresh after send failed", error=str(exc))

        debug["steps"] = steps
        debug["offer_action"] = "created"
        return True, None, debug
    except Exception as exc:
        logger.warning("FL.ru offer send failed", error=str(exc), url=job_url)
        return False, str(exc), {**debug, "steps": steps, "error_label": "send_failed"}
    finally:
        if playwright is not None and browser is not None:
            await close_browser(playwright, browser)
