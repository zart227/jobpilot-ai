from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.config import get_settings
from app.utils.formatting import competitive_offer_price

logger = structlog.get_logger(__name__)

LOGIN_URL = "https://kwork.ru/login"
DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
BLOCKED_TITLE = "Доступ заблокирован"
DEBUG_SCREENSHOT_DIR = Path("data/kwork_debug")


async def launch_browser() -> tuple[Playwright, Browser, Page]:
    settings = get_settings()
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context_kwargs: dict[str, Any] = {
        "viewport": DESKTOP_VIEWPORT,
        "locale": "ru-RU",
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    storage_state = settings.kwork_storage_state.strip()
    if storage_state and Path(storage_state).is_file():
        context_kwargs["storage_state"] = storage_state
        logger.info("Kwork browser using saved storage state", path=storage_state)

    context: BrowserContext = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    return playwright, browser, page


async def close_browser(playwright: Playwright, browser: Browser) -> None:
    await browser.close()
    await playwright.stop()


async def dismiss_overlays(page: Page) -> None:
    for selector in (
        'button:has-text("Окей!")',
        'button:has-text("Позже")',
        'button:has-text("Не сейчас")',
        ".modal-close",
        ".k-modal__close",
    ):
        locator = page.locator(selector)
        count = await locator.count()
        for index in range(count):
            button = locator.nth(index)
            if await button.is_visible():
                try:
                    await button.click(timeout=2000)
                    await page.wait_for_timeout(300)
                except Exception:
                    continue


async def _is_access_blocked(page: Page) -> bool:
    title = await page.title()
    if BLOCKED_TITLE in title:
        return True
    body = await page.inner_text("body")
    return "заблокирован доступ к сайту" in body.lower()


async def _has_active_session(page: Page) -> bool:
    await page.goto("https://kwork.ru/seller", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    if _is_on_login_page(page.url):
        return False
    if await _is_access_blocked(page):
        return False
    return True


def _is_on_login_page(url: str) -> bool:
    return "/login" in url


async def login(page: Page, email: str, password: str) -> bool:
    if await _has_active_session(page):
        logger.info("Kwork session already active")
        return True

    if not email or not password:
        settings = get_settings()
        if settings.kwork_storage_state.strip() and Path(settings.kwork_storage_state).is_file():
            logger.error("Kwork login failed: saved session expired, re-export kwork_session.json")
        else:
            logger.error("Kwork login failed: credentials are missing")
        return False

    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(3000)
    if await _is_access_blocked(page):
        logger.error(
            "Kwork blocked automated access from this IP. "
            "Export browser session to KWORK_STORAGE_STATE or contact Kwork support."
        )
        return False

    login_selector = 'input[placeholder="Электронная почта или логин"]'
    try:
        await page.wait_for_selector(login_selector, state="visible", timeout=30000)
    except Exception:
        if await _is_access_blocked(page):
            logger.error("Kwork blocked automated access from this IP")
            return False
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await page.wait_for_selector(login_selector, state="visible", timeout=30000)
    await dismiss_overlays(page)

    login_input = page.locator('input[placeholder="Электронная почта или логин"]')
    password_input = page.locator('input[placeholder="Пароль"]')
    await login_input.click()
    await login_input.fill(email)
    await password_input.click()
    await password_input.fill(password)

    if not await login_input.input_value():
        logger.error("Kwork login failed: login field stayed empty")
        return False

    await page.locator('button:has-text("Войти")').first.click()
    try:
        await page.wait_for_url(lambda url: "/login" not in url, timeout=20000)
    except Exception:
        errors = [
            text.strip()
            for text in await page.locator(".k-input__error, .error-message").all_inner_texts()
            if text.strip()
        ]
        logger.error("Kwork login failed", url=page.url, errors=errors)
        return False

    logger.info("Kwork login successful", url=page.url)
    return True


def _extract_project_id(job_url: str) -> str | None:
    match = re.search(r"/projects/(\d+)", job_url)
    return match.group(1) if match else None


def _offer_form_url(job_url: str) -> str | None:
    project_id = _extract_project_id(job_url)
    if not project_id:
        return None
    return f"https://kwork.ru/new_offer?project={project_id}"


def _normalize_project_url(job_url: str) -> str:
    url = job_url.rstrip("/")
    if not url.endswith("/view"):
        url = f"{url}/view"
    return url



async def _fill_trumbowyg(page: Page, placeholder_part: str, value: str) -> None:
    editor = page.locator(f'.trumbowyg-editor[placeholder*="{placeholder_part}"]')
    await editor.click(force=True)
    await editor.fill(value)
    field_name = await page.evaluate(
        """(part) => {
            const editor = [...document.querySelectorAll(".trumbowyg-editor")]
                .find((el) => (el.getAttribute("placeholder") || "").includes(part));
            const textarea = editor?.closest(".trumbowyg-box")?.querySelector("textarea");
            return textarea?.getAttribute("name") || "";
        }""",
        placeholder_part,
    )
    if field_name:
        await page.locator(f'textarea[name="{field_name}"]').fill(value, force=True)


async def _offer_form_ready(page: Page) -> bool:
    has_description = await page.locator('textarea[name="description"]').count() > 0
    has_submit = await page.get_by_role("button", name="Предложить").count() > 0
    return has_description and has_submit


async def _get_seller_state(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        """() => {
            const state = window.bus?.state || {};
            const user = state.user || {};
            return {
                confirmed: state.isUserConfirmedSeller,
                blocked: state.isActorBlocked,
                username: user.username ?? null,
                is_seller: user.is_seller ?? null,
                kwork_allow_status: user.kwork_allow_status ?? null,
                user_status: user.status ?? null,
                has_seller_onboarding_step: state.hasSellerOnboardingStep ?? null,
                needs_onboarding_popup:
                    state.isNeedShowSellerOnboardingVerifiedPopup
                    || state.isNeedShowSellerOnboardingFailedPopup,
                page_url: location.href,
            };
        }"""
    )


async def _save_debug_screenshot(page: Page, label: str) -> str | None:
    """Save a Playwright screenshot for Kwork automation debugging."""
    try:
        DEBUG_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label)[:80]
        path = DEBUG_SCREENSHOT_DIR / f"{timestamp}_{safe_label}.png"
        await page.screenshot(path=str(path), full_page=True)
        logger.info("Kwork debug screenshot saved", path=str(path), label=label, url=page.url)
        return str(path)
    except Exception as exc:
        logger.warning("Kwork debug screenshot failed", label=label, error=str(exc))
        return None


async def _load_seller_profile_if_needed(page: Page, info: dict[str, Any]) -> dict[str, Any]:
    """Offer form pages often omit user fields in window.bus — load them from /seller."""
    if info.get("is_seller") or info.get("kwork_allow_status"):
        return info

    current_url = page.url
    logger.info("Kwork seller profile missing on page, loading /seller", url=current_url)
    await page.goto("https://kwork.ru/seller", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    profile = await _get_seller_state(page)
    logger.info(
        "Kwork seller profile loaded",
        username=profile.get("username"),
        is_seller=profile.get("is_seller"),
        kwork_allow_status=profile.get("kwork_allow_status"),
        confirmed=profile.get("confirmed"),
    )

    if current_url and current_url != page.url:
        await page.goto(current_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
    return {**info, **profile}


async def _collect_visible_form_errors(page: Page) -> list[str]:
    return [
        text.strip()
        for text in await page.locator(
            ".form-item__error:visible, .k-input__error:visible, "
            ".error-message:visible, .form-error:visible"
        ).all_inner_texts()
        if text.strip() and len(text.strip()) < 200
    ]


async def _exchange_lesson_required(page: Page) -> bool:
    """Detect a hard block that prevents submitting exchange offers."""
    blocking_selectors = (
        ".modal:has-text('урок по работе на бирже')",
        ".k-modal:has-text('урок по работе на бирже')",
        ".popup:has-text('урок по работе на бирже')",
        ".form-item__error:has-text('урок')",
        ".error-message:has-text('урок')",
    )
    for selector in blocking_selectors:
        locator = page.locator(selector)
        if await locator.count() > 0 and await locator.first.is_visible():
            return True

    submit_button = page.get_by_role("button", name="Предложить")
    form_ready = await _offer_form_ready(page)
    if form_ready and await submit_button.first.is_enabled():
        return False

    body = (await page.inner_text("body")).lower()
    return (
        "сначала пройдите урок" in body
        or "необходимо пройти урок" in body
        or "чтобы откликаться, пройдите урок" in body
    )


async def _verify_offer_in_my_offers(page: Page, project_id: str) -> bool:
    await page.goto(
        "https://kwork.ru/manage_orders?tab=offers",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    await page.wait_for_timeout(2500)
    content = await page.content()
    return f"/projects/{project_id}" in content or f"project={project_id}" in content


async def _diagnose_offer_blocker(page: Page, job_url: str) -> str | None:
    """Detect why Kwork won't open the offer form for this project."""
    view_url = _normalize_project_url(job_url)
    await page.goto(view_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)

    resume_error = page.locator(".js-link-resume-error:has-text('Предложить услугу')")
    if await resume_error.count() > 0:
        await resume_error.first.click(force=True)
        await page.wait_for_timeout(2000)
        body = (await page.inner_text("body")).lower()
        if "портфолио" in body and "кворк" in body:
            return (
                "Для этой рубрики Kwork требует кворк с портфолио. "
                "Создайте кворк и загрузите работы: kwork.ru/seller"
            )
        if "портфолио" in body:
            return "Для откликов в этой рубрике нужно портфолио на Kwork"

    body = (await page.inner_text("body")).lower()
    if "архив" in body or "закрыт" in body:
        return "Заказ закрыт или в архиве на Kwork"
    return None


async def _check_seller_ready(page: Page) -> tuple[bool, str | None]:
    info = await _load_seller_profile_if_needed(page, await _get_seller_state(page))
    form_ready = await _offer_form_ready(page)
    logger.info(
        "Kwork seller readiness check",
        confirmed=info.get("confirmed"),
        blocked=info.get("blocked"),
        username=info.get("username"),
        is_seller=info.get("is_seller"),
        kwork_allow_status=info.get("kwork_allow_status"),
        has_seller_onboarding_step=info.get("has_seller_onboarding_step"),
        offer_form_ready=form_ready,
        page_url=info.get("page_url"),
    )

    if info.get("blocked"):
        return False, "Аккаунт продавца заблокирован на Kwork"

    is_active_seller = (
        info.get("is_seller") == "1" or info.get("kwork_allow_status") == "allow"
    )
    if is_active_seller:
        return True, None

    # new_offer often has isUserConfirmedSeller=false for legacy sellers while the form works.
    if form_ready:
        logger.info(
            "Kwork offer form available, ignoring isUserConfirmedSeller flag",
            confirmed=info.get("confirmed"),
            username=info.get("username"),
        )
        return True, None

    onboarding_required = (
        info.get("has_seller_onboarding_step") is True
        or info.get("needs_onboarding_popup") is True
    )
    if info.get("confirmed") is False and onboarding_required:
        logger.error(
            "Kwork seller onboarding is required",
            username=info.get("username"),
            has_seller_onboarding_step=info.get("has_seller_onboarding_step"),
        )
        return False, (
            "Профиль продавца не подтверждён — пройдите онбординг на kwork.ru/seller"
        )

    if info.get("confirmed") is False:
        logger.warning(
            "Kwork isUserConfirmedSeller=false but no blocking onboarding step detected",
            username=info.get("username"),
        )

    return True, None


async def _fill_offer_form(
    page: Page,
    content: str,
    budget_min: float | None,
    budget_max: float | None,
) -> None:
    if len(content.strip()) < 150:
        content = f"{content.strip()}\n\nГотов приступить к задаче в ближайшее время."

    await _fill_trumbowyg(page, "решать задачу", content)

    settings = get_settings()
    price = competitive_offer_price(
        budget_min,
        budget_max,
        discount_percent=settings.kwork_offer_discount_percent,
    ) or 5000
    logger.info(
        "Kwork competitive offer price",
        desired_budget=budget_min if budget_min is not None else budget_max,
        allowable_cap=budget_max if budget_min is not None else None,
        offer_price=price,
        discount_percent=settings.kwork_offer_discount_percent,
    )

    price_input = page.locator('input[placeholder*="000"]').first
    await price_input.click()
    await price_input.fill(str(price))
    await page.wait_for_timeout(500)

    await _select_duration(page)

    payment_item = page.locator(".offer-payment-type__item").first
    if await payment_item.count() > 0:
        await payment_item.click()
        await page.wait_for_timeout(500)

    want_title = await page.evaluate(
        """() => window.bus?.state?.want?.title || "Выполнение проекта" """
    )
    await _fill_trumbowyg(page, "Введите название заказа", want_title[:200])


async def _select_duration(page: Page) -> None:
    duration_input = page.locator('input[placeholder="Срок выполнения"]')
    if await duration_input.count() == 0:
        return

    await duration_input.click(force=True)
    await page.wait_for_timeout(500)

    preferred = page.locator('.vs__dropdown-option:has-text("7"), [role="option"]:has-text("7")')
    if await preferred.count() > 0:
        await preferred.first.click()
        return

    fallback = page.locator('.vs__dropdown-option, [role="option"]')
    if await fallback.count() > 0:
        await fallback.first.click()


async def _fail_offer(
    page: Page,
    label: str,
    error: str,
    *,
    url: str | None = None,
    **log_kwargs: Any,
) -> tuple[bool, str | None]:
    screenshot = await _save_debug_screenshot(page, label)
    logger.error(
        "Kwork offer step failed",
        error=error,
        screenshot=screenshot,
        url=url or page.url,
        **log_kwargs,
    )
    return False, error


async def submit_offer(
    page: Page,
    job_url: str,
    content: str,
    budget_min: float | None = None,
    budget_max: float | None = None,
) -> tuple[bool, str | None]:
    project_id = _extract_project_id(job_url)
    offer_url = _offer_form_url(job_url)
    if not offer_url or not project_id:
        logger.error("Kwork project id not found in url", url=job_url)
        return False, "Не удалось определить ID проекта Kwork"

    logger.info(
        "Kwork opening offer form",
        job_url=job_url,
        offer_url=offer_url,
        project_id=project_id,
        content_length=len(content),
    )

    await page.goto(offer_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)

    if await _is_access_blocked(page):
        return await _fail_offer(page, "access_blocked", "Kwork заблокировал доступ с этого IP", url=offer_url)

    if "/login" in page.url:
        return await _fail_offer(
            page,
            "session_expired",
            "Сессия Kwork устарела — пересохраните data/kwork_session.json",
            url=page.url,
        )

    if "new_offer" not in page.url:
        if await _verify_offer_in_my_offers(page, project_id):
            logger.info("Kwork offer already exists in my offers", project_id=project_id)
            return True, None
        blocker = await _diagnose_offer_blocker(page, job_url)
        if blocker:
            return await _fail_offer(page, "offer_blocked", blocker, url=offer_url, reason=blocker)
        return await _fail_offer(
            page,
            "form_unavailable",
            "Форма отклика недоступна на Kwork",
            url=offer_url,
            redirect=page.url,
        )

    ready, ready_error = await _check_seller_ready(page)
    if not ready:
        return await _fail_offer(
            page,
            "seller_not_ready",
            ready_error or "Профиль продавца не готов к откликам",
            url=offer_url,
        )

    if await _exchange_lesson_required(page):
        return await _fail_offer(
            page,
            "exchange_lesson_required",
            "Пройдите урок по работе на Бирже на Kwork",
            url=offer_url,
        )

    textarea = page.locator('textarea[name="description"]')
    try:
        await textarea.wait_for(state="attached", timeout=20000)
    except Exception:
        return await _fail_offer(
            page,
            "form_not_loaded",
            "Форма отклика не загрузилась",
            url=offer_url,
            current_url=page.url,
        )

    await _fill_offer_form(page, content, budget_min, budget_max)
    await _save_debug_screenshot(page, f"before_submit_{project_id}")

    submit_button = page.get_by_role("button", name="Предложить")
    if await submit_button.count() == 0:
        return await _fail_offer(
            page,
            "submit_button_missing",
            "Кнопка «Предложить» не найдена на форме",
            url=offer_url,
        )

    await submit_button.scroll_into_view_if_needed()
    await submit_button.click(force=True)
    await page.wait_for_timeout(5000)

    if "new_offer" in page.url:
        errors = await _collect_visible_form_errors(page)
        body = (await page.inner_text("body")).lower()
        if errors:
            return await _fail_offer(
                page,
                "form_validation_error",
                errors[0],
                url=offer_url,
                errors=errors,
            )
        if await _exchange_lesson_required(page):
            return await _fail_offer(
                page,
                "exchange_lesson_after_submit",
                "Пройдите урок по работе на Бирже на Kwork",
                url=offer_url,
            )
        if "не подтвержден" in body or "онбординг" in body:
            return await _fail_offer(
                page,
                "onboarding_required_after_submit",
                "Профиль продавца не подтверждён на Kwork",
                url=offer_url,
            )
        return await _fail_offer(
            page,
            "stayed_on_form",
            "Kwork не принял отклик — форма осталась открытой",
            url=offer_url,
            current_url=page.url,
        )

    if await _verify_offer_in_my_offers(page, project_id):
        logger.info("Kwork offer verified in my offers", project_id=project_id)
        return True, None

    body = (await page.inner_text("body")).lower()
    if any(
        marker in body
        for marker in (
            "отклик отправлен",
            "предложение отправлено",
            "ваше предложение отправлено",
        )
    ):
        logger.info("Kwork offer submitted", url=offer_url)
        return True, None

    screenshot = await _save_debug_screenshot(page, f"unverified_submit_{project_id}")
    logger.warning(
        "Kwork offer not found in my offers after submit",
        url=offer_url,
        current_url=page.url,
        screenshot=screenshot,
    )
    return False, "Отклик не появился в «Мои отклики» — отправка не подтверждена"


async def send_kwork_offer(
    job_url: str,
    content: str,
    budget_min: float | None = None,
    budget_max: float | None = None,
) -> tuple[bool, str | None]:
    settings = get_settings()
    playwright: Playwright | None = None
    browser: Browser | None = None

    try:
        playwright, browser, page = await launch_browser()
        logged_in = await login(page, settings.kwork_email, settings.kwork_password)
        if not logged_in:
            await _save_debug_screenshot(page, "login_failed")
            return False, "Не удалось войти в Kwork — проверьте сессию"
        logger.info(
            "Kwork login ok, submitting offer",
            job_url=job_url,
            storage_state=settings.kwork_storage_state or None,
        )
        return await submit_offer(page, job_url, content, budget_min, budget_max)
    except Exception as exc:
        logger.error("Kwork offer send failed", error=str(exc), url=job_url, exc_type=type(exc).__name__)
        return False, f"Ошибка отправки: {exc}"
    finally:
        if playwright and browser:
            await close_browser(playwright, browser)
