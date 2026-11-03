from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.config import get_settings
from app.services.kwork_pause import (
    connects_balance_fields,
    connects_limit_exhausted,
    extract_connects_replenish_text,
    record_connects_limit_detected,
)
from app.utils.formatting import (
    KWORK_MAX_OFFER_CHARS,
    extract_site_order_id,
)
from app.utils.storage_state import save_browser_storage_state

logger = structlog.get_logger(__name__)

LOGIN_URL = "https://kwork.ru/login"
DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
BLOCKED_TITLE = "Доступ заблокирован"
DEBUG_SCREENSHOT_DIR = Path("data/kwork_debug")
_kwork_send_lock = asyncio.Lock()

KWORK_ORDER_CLOSED_ERROR = "Заказ закрыт на Kwork — исполнитель уже выбран, отклики не принимаются"
KWORK_CONNECTS_LIMIT_ERROR = (
    "Исчерпан лимит коннектов на Kwork — новые отклики на Бирже недоступны "
    "до ежемесячного пополнения"
)
UNAPPROVED_OPTIONAL_CONTROLS = (
    '[name="payment_type"], [name="paymentType"], [name="title"], [name="kwork_title"], '
    'input[name="name"], textarea[name="name"], .offer-payment-type__item, '
    '.trumbowyg-editor[placeholder*="название"]'
)


def is_kwork_order_closed_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(
        phrase in lowered
        for phrase in (
            "исполнитель уже выбран",
            "закрыт на kwork",
            "закрыт или в архиве",
            "проект уже закрыт",
        )
    )


def is_kwork_connects_limit_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    return any(
        phrase in lowered
        for phrase in (
            "лимит коннектов",
            "исчерпали лимит коннект",
            "connects_limit",
        )
    )


CONNECTS_BALANCE_RE = re.compile(r"(\d+)\s*коннект", re.IGNORECASE)


def _connects_balance_available(body: str) -> bool:
    """True when page shows positive connects balance."""
    if connects_limit_exhausted(body):
        return False
    fields = connects_balance_fields(body)
    remaining = fields.get("connects_remaining")
    if remaining is not None:
        return remaining > 0
    match = CONNECTS_BALANCE_RE.search(body)
    if match:
        return int(match.group(1)) > 0
    return False


async def _read_connects_balance_from_page(page: Page) -> dict[str, int]:
    texts = await _visible_page_texts(page)
    fields = connects_balance_fields(texts)
    if fields:
        return fields

    settings = get_settings()
    listing_url = settings.kwork_category_url or "https://kwork.ru/projects?a=1"
    try:
        await page.goto(listing_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
    except Exception:
        return {}
    return connects_balance_fields(await _visible_page_texts(page))


async def _attach_connects_balance_to_debug(
    page: Page,
    debug: dict[str, Any] | None,
) -> dict[str, Any]:
    enriched = dict(debug or {})
    enriched.update(await _read_connects_balance_from_page(page))
    return enriched


async def _offer_submit_success(
    page: Page,
    *,
    job_url: str,
    content: str,
    content_file: str | None,
    steps: list[str],
    offer_price: int | None,
) -> tuple[bool, None, dict[str, Any]]:
    debug = await _attach_connects_balance_to_debug(
        page,
        {
            "job_url": job_url,
            "content_length": len(content),
            "content_file": content_file,
            "steps": steps,
            "offer_action": "created",
            "offer_price": offer_price,
        },
    )
    return True, None, debug


def _order_closed_in_body(body: str) -> bool:
    lowered = body.lower()
    return any(
        phrase in lowered
        for phrase in (
            "исполнитель уже выбран",
            "заказ закрыт",
            "проект закрыт",
            "проект уже закрыт",
            "находится в архиве",
            "заказ в архиве",
            "проект в архиве",
        )
    )


def _propose_offer_locators(page: Page):
    return (
        page.get_by_text("Предложить услугу", exact=True),
        page.locator('text=Предложить услугу'),
        page.locator('.js-link-resume-error:has-text("Предложить услугу")'),
        page.locator('span.kw-button:has-text("Предложить услугу")'),
        page.get_by_role("button", name="Предложить услугу"),
        page.get_by_role("link", name="Предложить услугу"),
        page.locator('button:has-text("Предложить услугу")'),
        page.locator('a:has-text("Предложить услугу")'),
    )


async def _visible_page_texts(page: Page) -> str:
    parts = [await page.inner_text("body")]
    for text in await page.locator(
        ".tooltip:visible, .popover:visible, [role='tooltip']:visible, "
        ".v-tooltip:visible, .modal:visible, .popup:visible"
    ).all_inner_texts():
        if text.strip():
            parts.append(text)
    return "\n".join(parts)


async def _detect_connects_limit_on_page(page: Page) -> bool:
    if connects_limit_exhausted(await _visible_page_texts(page)):
        return True

    for locator in _propose_offer_locators(page):
        if await locator.count() == 0:
            continue
        button = locator.first
        if not await button.is_visible():
            continue
        try:
            await button.hover(timeout=3000)
            await page.wait_for_timeout(600)
        except Exception:
            pass
        if connects_limit_exhausted(await _visible_page_texts(page)):
            return True
        break
    return False


def format_connects_limit_error(replenish_date: str | None = None) -> str:
    message = KWORK_CONNECTS_LIMIT_ERROR
    if replenish_date:
        message = (
            f"Исчерпан лимит коннектов на Kwork — новые отклики на Бирже недоступны "
            f"до {replenish_date}"
        )
    return message


async def _connects_limit_error_from_page(page: Page) -> str:
    texts = await _visible_page_texts(page)
    return format_connects_limit_error(extract_connects_replenish_text(texts))


async def launch_browser(*, proxy: str | None = None) -> tuple[Playwright, Browser, Page]:
    settings = get_settings()
    playwright = await async_playwright().start()
    launch_kwargs: dict[str, Any] = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    browser = None
    try:
        browser = await playwright.chromium.launch(**launch_kwargs)
        context_kwargs: dict[str, Any] = {
            "viewport": DESKTOP_VIEWPORT,
            "locale": "ru-RU",
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        if proxy:
            from urllib.parse import urlparse

            parsed = urlparse(proxy)
            proxy_config: dict[str, str] = {
                "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            }
            if parsed.username:
                proxy_config["username"] = parsed.username
            if parsed.password:
                proxy_config["password"] = parsed.password
            context_kwargs["proxy"] = proxy_config
            logger.info("Kwork browser using proxy", proxy_host=parsed.hostname)
        storage_state = settings.kwork_storage_state.strip()
        if storage_state and Path(storage_state).is_file():
            context_kwargs["storage_state"] = storage_state
            logger.info("Kwork browser using saved storage state", path=storage_state)

        context: BrowserContext = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        return playwright, browser, page
    except BaseException:
        if browser is not None:
            await browser.close()
        await playwright.stop()
        raise


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
    settings = get_settings()
    storage_state = settings.kwork_storage_state.strip()
    has_saved_session = bool(storage_state and Path(storage_state).is_file())
    url, timeout_ms = (
        ("https://kwork.ru/", 30000) if has_saved_session else ("https://kwork.ru/seller", 60000)
    )
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(1500)
    except Exception as exc:
        logger.warning("Kwork session check failed", url=url, error=str(exc))
        return False
    if _is_on_login_page(page.url):
        return False
    return not await _is_access_blocked(page)


def _is_on_login_page(url: str) -> bool:
    return "/login" in url


async def login(page: Page, email: str, password: str) -> bool:
    settings = get_settings()
    storage_state = settings.kwork_storage_state.strip()
    if storage_state and Path(storage_state).is_file():
        logger.info("Kwork using saved storage state, skipping network session check")
        return True

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


async def _fill_trumbowyg(page: Page, placeholder_part: str, value: str, *, require_visible: bool = True) -> None:
    editor = page.locator(f'.trumbowyg-editor[placeholder*="{placeholder_part}"]')
    if await editor.count() != 1:
        raise RuntimeError("Редактор формы отсутствует или неоднозначен")
    await editor.wait_for(state="visible" if require_visible else "attached", timeout=30000)
    await editor.click(force=True)
    await editor.fill(value)
    # Playwright keyboard fill cannot reliably focus a hidden Trumbowyg textarea.
    # Set its native value and publish the events consumed by reactive forms.
    await editor.evaluate(
        """(editor, value) => {
        const fields = editor.closest('.trumbowyg-box')?.querySelectorAll('textarea');
        if (!fields || fields.length !== 1) throw new Error('Ambiguous editor textarea');
        const textarea = fields[0];
        // Native contenteditable filling can introduce <div>/<br> line breaks.
        // Keep the plain-text DOM and backing value identical to the approval.
        editor.textContent = value;
        editor.dispatchEvent(new Event('input', {bubbles: true}));
        editor.dispatchEvent(new Event('change', {bubbles: true}));
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
        setter.call(textarea, value);
        textarea.dispatchEvent(new Event('input', {bubbles: true}));
        textarea.dispatchEvent(new Event('change', {bubbles: true}));
    }""",
        value,
    )


async def _fill_price_input(page: Page, price: int) -> tuple[bool, str | None]:
    """Fill Kwork offer price field; form markup varies by project category."""
    await page.wait_for_timeout(800)
    method = await page.evaluate(
        """(price) => {
            const setValue = (input) => {
                input.focus();
                input.value = String(price);
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dispatchEvent(new Event("change", { bubbles: true }));
            };

            const byName = document.querySelector('input[name="price"], input[name="kwork_price"]');
            if (byName) {
                setValue(byName);
                return "name";
            }

            for (const input of document.querySelectorAll("input")) {
                const placeholder = input.placeholder || "";
                if (/0{2,}/.test(placeholder) || /стоимость|цена/i.test(placeholder)) {
                    setValue(input);
                    return "placeholder";
                }
            }

            for (const block of document.querySelectorAll(
                ".form-item, .offer-form__row, .offer-form__item, .kw-field, fieldset, label"
            )) {
                const text = (block.textContent || "").toLowerCase();
                if (!text.includes("стоимость") && !text.includes("цена")) {
                    continue;
                }
                const input = block.querySelector("input:not([type='hidden'])");
                if (input) {
                    setValue(input);
                    return "label";
                }
            }

            const visibleInputs = [...document.querySelectorAll(
                ".offer-form input[type='tel'], .offer-form input[type='number'], .new-offer input[type='tel']"
            )].filter((el) => el.offsetParent !== null);
            if (visibleInputs.length === 1) {
                setValue(visibleInputs[0]);
                return "single_tel";
            }

            return null;
        }""",
        price,
    )
    if method:
        logger.info("Kwork price input filled", method=method, offer_price=price)
        await page.wait_for_timeout(400)
        return True, method

    playwright_selectors = (
        'input[name="price"]',
        'input[name="kwork_price"]',
        'input[placeholder*="000"]',
        '.form-item:has-text("Стоимость") input',
        '.form-item:has-text("Цена") input',
    )
    for selector in playwright_selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() == 0:
                continue
            await locator.wait_for(state="visible", timeout=3000)
            await locator.click(force=True)
            await locator.fill(str(price))
            logger.info("Kwork price input filled", method=selector, offer_price=price)
            await page.wait_for_timeout(400)
            return True, selector
        except Exception:
            continue

    return False, None


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


def _debug_base_name(label: str) -> tuple[str, str]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label)[:80]
    return timestamp, f"{timestamp}_{safe_label}"


def _save_submission_content(content: str, label: str) -> str | None:
    """Save full offer text to a separate file; logs reference this path only."""
    try:
        DEBUG_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        _, base = _debug_base_name(label)
        path = DEBUG_SCREENSHOT_DIR / f"{base}_content.txt"
        path.write_text(content, encoding="utf-8")
        return str(path)
    except Exception as exc:
        logger.warning("Kwork submission content save failed", label=label, error=str(exc))
        return None


async def _save_debug_context(
    label: str,
    *,
    job_url: str,
    content: str,
    steps: list[str],
    error: str,
    screenshot: str | None = None,
    content_file: str | None = None,
    **extra: Any,
) -> str | None:
    """Persist JSON debug dump for failed Kwork offer submissions."""
    try:
        DEBUG_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        _, base = _debug_base_name(label)
        path = DEBUG_SCREENSHOT_DIR / f"{base}.json"
        if not content_file and content:
            content_file = _save_submission_content(content, label)
        payload = {
            "timestamp": base.split("_", 1)[0],
            "label": label,
            "job_url": job_url,
            "content_length": len(content),
            "content_file": content_file,
            "steps": steps,
            "error": error,
            "screenshot": screenshot,
            **extra,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(
            "Kwork debug context saved",
            path=str(path),
            label=label,
            content_file=content_file,
            content_length=len(content),
        )
        return str(path)
    except Exception as exc:
        logger.warning("Kwork debug context save failed", label=label, error=str(exc))
        return None


async def _save_debug_screenshot(page: Page, label: str) -> str | None:
    """Save a Playwright screenshot for Kwork automation debugging."""
    try:
        DEBUG_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        _, base = _debug_base_name(label)
        path = DEBUG_SCREENSHOT_DIR / f"{base}.png"
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
    errors = [
        text.strip()
        for text in await page.locator(
            ".form-item__error:visible, .k-input__error:visible, "
            ".error-message:visible, .form-error:visible, "
            ".offer-form__error:visible, .new-offer__error:visible"
        ).all_inner_texts()
        if text.strip() and len(text.strip()) < 200
    ]
    body = (await page.inner_text("body")).lower()
    if connects_limit_exhausted(body) and not any(
        is_kwork_connects_limit_error(error) for error in errors
    ):
        errors.append(KWORK_CONNECTS_LIMIT_ERROR)
    return errors


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


async def _verify_offer_on_project_page(page: Page, project_id: str) -> bool:
    """Check project view page for signs that our offer was submitted."""
    view_url = f"https://kwork.ru/projects/{project_id}/view"
    if project_id not in page.url:
        await page.goto(view_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)

    body = (await page.inner_text("body")).lower()
    success_markers = (
        "ваше предложение",
        "вы предложили",
        "редактировать предложение",
        "отозвать предложение",
        "ваш отклик",
        "отклик отправлен",
        "предложение отправлено",
    )
    if any(marker in body for marker in success_markers):
        logger.info("Kwork offer verified on project page", project_id=project_id)
        return True

    return False


async def _verify_offer_after_submit_redirect(page: Page, project_id: str) -> bool:
    """Lenient check right after submit when Kwork left the offer form."""
    if "new_offer" in page.url:
        return False
    if "not_access.php" in page.url or await _is_access_blocked(page):
        return False
    if "/login" in page.url:
        return False

    await page.wait_for_timeout(2000)
    current_url = page.url.lower()
    body = (await page.inner_text("body")).lower()
    if any(
        marker in body
        for marker in (
            "отклик отправлен",
            "предложение отправлено",
            "ваше предложение отправлено",
        )
    ):
        logger.info("Kwork offer verified via success message", project_id=project_id)
        return True

    if any(part in current_url for part in ("tab=offers", "manage_orders")):
        logger.info(
            "Kwork offer verified via offers redirect",
            project_id=project_id,
            url=page.url,
        )
        return True

    if current_url.rstrip("/").endswith("/projects"):
        logger.info(
            "Kwork offer verified via projects hub redirect",
            project_id=project_id,
            url=page.url,
        )
        return True

    html = await page.content()
    project_markers = (
        f"/projects/{project_id}/view",
        f"/projects/{project_id}",
        f"project={project_id}",
    )
    if any(marker in html for marker in project_markers):
        return await _verify_offer_on_project_page(page, project_id)

    return False


async def _verify_offer_in_my_offers(page: Page, project_id: str) -> bool:
    """Check project page and offers lists for a submitted offer."""
    view_url = f"https://kwork.ru/projects/{project_id}/view"
    await page.goto(view_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    if await _verify_offer_on_project_page(page, project_id):
        return True

    offers_urls = (
        "https://kwork.ru/projects?tab=offers",
        "https://kwork.ru/manage_orders?tab=offers",
    )
    link_selector = (
        f'a[href*="/projects/{project_id}/view"], '
        f'a[href*="/projects/{project_id}"], '
        f'a[href*="project={project_id}"]'
    )

    for offers_url in offers_urls:
        await page.goto(offers_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        for _ in range(6):
            links = page.locator(link_selector)
            count = await links.count()
            if count > 0:
                logger.info(
                    "Kwork offer found in offers list by project link",
                    project_id=project_id,
                    offers_url=offers_url,
                    link_count=count,
                )
                return True
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1200)

    return False


async def _open_new_offer_from_project_page(
    page: Page,
    job_url: str,
    steps: list[str],
) -> bool:
    view_url = _normalize_project_url(job_url)
    steps.append(f"открытие страницы заказа: {view_url}")
    await page.goto(view_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)

    propose_locators = _propose_offer_locators(page)
    clicked = False
    for locator in propose_locators:
        if await locator.count() == 0:
            continue
        button = locator.first
        try:
            await button.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass
        if not await button.is_visible():
            continue
        steps.append("нажатие «Предложить услугу» на странице заказа")
        await button.click(force=True)
        await page.wait_for_timeout(2500)
        clicked = True
        break

    if not clicked:
        if await _detect_connects_limit_on_page(page):
            steps.append(f"блокировка Kwork: {await _connects_limit_error_from_page(page)}")
        return False

    if await _offer_form_visible(page):
        return True

    if await _detect_connects_limit_on_page(page):
        steps.append(f"блокировка Kwork: {await _connects_limit_error_from_page(page)}")
        return False

    blocker = await _diagnose_offer_blocker(page, job_url)
    if blocker:
        steps.append(f"блокировка Kwork: {blocker}")
    return False


async def _offer_form_visible(page: Page) -> bool:
    textarea = page.locator('textarea[name="description"]')
    if await textarea.count() > 0:
        try:
            await textarea.first.wait_for(state="visible", timeout=5000)
            return True
        except Exception:
            pass
    return await _offer_form_ready(page)


async def _open_edit_offer_form(
    page: Page,
    project_id: str,
    job_url: str,
    steps: list[str],
) -> bool:
    edit_urls = (
        f"https://kwork.ru/edit_offer?project={project_id}",
        f"https://kwork.ru/new_offer?project={project_id}&edit=1",
    )
    for edit_url in edit_urls:
        steps.append(f"попытка открыть редактирование: {edit_url}")
        await page.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        if await _offer_form_visible(page):
            steps.append(f"форма редактирования открыта: {page.url}")
            return True

    view_url = _normalize_project_url(job_url)
    steps.append(f"переход на страницу заказа: {view_url}")
    await page.goto(view_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)

    edit_locators = (
        page.get_by_role("link", name=re.compile(r"редактировать\s+предложение", re.IGNORECASE)),
        page.get_by_role("button", name=re.compile(r"редактировать\s+предложение", re.IGNORECASE)),
        page.locator('a[href*="edit_offer"]'),
        page.locator('a[href*="new_offer"][href*="edit"]'),
    )
    for locator in edit_locators:
        if await locator.count() == 0:
            continue
        await locator.first.click(force=True)
        await page.wait_for_timeout(2500)
        if await _offer_form_visible(page):
            steps.append(f"форма редактирования открыта через UI: {page.url}")
            return True

    return False


async def _click_submit_offer_button(page: Page) -> str | None:
    description = page.locator('textarea[name="description"]')
    if await description.count() != 1:
        return None
    form = description.locator('xpath=ancestor::form[1]')
    if await form.count() != 1:
        return None
    candidates = []
    for label in ("Предложить", "Сохранить", "Сохранить изменения", "Отправить"):
        button = form.get_by_role("button", name=label, exact=True)
        for index in range(await button.count()):
            candidates.append((label, button.nth(index)))
    if len(candidates) != 1:
        return None
    label, button = candidates[0]
    if not await button.is_visible() or not await button.is_enabled():
        return None
    await button.scroll_into_view_if_needed()
    await button.click()
    return label


async def _edit_existing_offer(
    page: Page,
    job_url: str,
    content: str,
    budget_min: float | None,
    budget_max: float | None,
    steps: list[str],
    project_id: str,
    *,
    content_file: str | None,
    offer_price: int | None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    return (
        False,
        "Редактирование требует отдельного approval flow",
        {"error_label": "edit_requires_approval"},
    )


async def _diagnose_offer_blocker(page: Page, job_url: str) -> str | None:
    """Detect why Kwork won't open the offer form for this project."""
    view_url = _normalize_project_url(job_url)
    await page.goto(view_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)

    if await _detect_connects_limit_on_page(page):
        error = await _connects_limit_error_from_page(page)
        record_connects_limit_detected(error, source="diagnose_offer")
        return error

    propose_locators = _propose_offer_locators(page)
    has_propose_button = False
    propose_button_disabled = False
    for locator in propose_locators:
        if await locator.count() == 0:
            continue
        has_propose_button = True
        button = locator.first
        if await button.is_visible() and await button.is_disabled():
            propose_button_disabled = True
        break

    review_locators = (
        page.get_by_role("button", name="Оставить отзыв"),
        page.get_by_role("link", name="Оставить отзыв"),
        page.locator('button:has-text("Оставить отзыв")'),
        page.locator('a:has-text("Оставить отзыв")'),
    )
    has_review_action = False
    for locator in review_locators:
        if await locator.count() > 0:
            has_review_action = True
            break

    if has_review_action and not has_propose_button:
        return KWORK_ORDER_CLOSED_ERROR

    resume_error = page.locator(".js-link-resume-error:has-text('Предложить услугу')")
    if await resume_error.count() > 0:
        btn = resume_error.first
        if await btn.is_visible():
            await btn.click(force=True)
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
    if propose_button_disabled and await _detect_connects_limit_on_page(page):
        error = await _connects_limit_error_from_page(page)
        record_connects_limit_detected(error, source="diagnose_offer_disabled")
        return error
    if _order_closed_in_body(body):
        return KWORK_ORDER_CLOSED_ERROR
    if not has_propose_button:
        if "оставить отзыв" in body:
            return KWORK_ORDER_CLOSED_ERROR
        return "Кнопка «Предложить услугу» отсутствует — форма отклика недоступна на Kwork"
    return None


async def _check_seller_ready(page: Page) -> tuple[bool, str | None]:
    form_ready = await _offer_form_ready(page)
    if form_ready:
        logger.info("Kwork offer form available, skipping seller onboarding check")
        return True, None

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

    is_active_seller = info.get("is_seller") == "1" or info.get("kwork_allow_status") == "allow"
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
        info.get("has_seller_onboarding_step") is True or info.get("needs_onboarding_popup") is True
    )
    if info.get("confirmed") is False and onboarding_required:
        logger.error(
            "Kwork seller onboarding is required",
            username=info.get("username"),
            has_seller_onboarding_step=info.get("has_seller_onboarding_step"),
        )
        return False, ("Профиль продавца не подтверждён — пройдите онбординг на kwork.ru/seller")

    if info.get("confirmed") is False:
        logger.warning(
            "Kwork isUserConfirmedSeller=false but no blocking onboarding step detected",
            username=info.get("username"),
        )

    return True, None


def validate_approved_submission(
    content: str, offer_price: int | None, duration_days: int | None
) -> None:
    if not 150 <= len(content) <= KWORK_MAX_OFFER_CHARS:
        raise ValueError("Одобренный текст должен содержать от150 до2000 символов")
    if type(offer_price) is not int or offer_price < 500:
        raise ValueError("Не задана одобренная цена")
    if type(duration_days) is not int or duration_days <= 0:
        raise ValueError("Не задан одобренный срок")


async def _verify_approved_form(
    page: Page, content: str, offer_price: int, duration_days: int
) -> None:
    actual = await page.evaluate(
        """({expectedContent, offerPrice}) => {
        if (document.querySelector('[name="payment_type"], [name="paymentType"], [name="title"], [name="kwork_title"], input[name="name"], textarea[name="name"], .offer-payment-type__item, .trumbowyg-editor[placeholder*="название"]'))
            return {error: 'unapproved_optional_control'};
        const one = selector => {
            const nodes = document.querySelectorAll(selector);
            return nodes.length === 1 ? nodes[0] : null;
        };
        const description = one('textarea[name="description"]');
        const editor = one('.trumbowyg-editor[placeholder*="решать задачу"]');
        if (!description || !editor)
            return {error: 'missing_or_ambiguous_control'};
        if (!editor.isContentEditable || editor.getClientRects().length === 0)
            return {error: 'editor_not_visible'};
        let priceInputs = [...document.querySelectorAll('input[name="price"], input[name="kwork_price"]')];
        if (!priceInputs.length)
            priceInputs = [...document.querySelectorAll('input[placeholder*="000"], input[placeholder*="цена"], input[placeholder*="стоимость"]')];
        const durationInput = one('input[placeholder="Срок выполнения"]');
        if (priceInputs.length !== 1 || !durationInput)
            return {error: 'missing_or_ambiguous_control'};
        if (priceInputs[0].type === 'hidden' || !priceInputs[0].getClientRects().length || priceInputs[0].disabled || !durationInput.getClientRects().length || durationInput.disabled)
            return {error: 'missing_or_ambiguous_control'};
        const form = description.closest('form');
        if (!form || editor.closest('form') !== form || priceInputs[0].closest('form') !== form || durationInput.closest('form') !== form || editor.closest('.trumbowyg-box') !== description.closest('.trumbowyg-box'))
            return {error: 'different_forms'};
        const select = durationInput.closest('.v-select');
        const selected = select ? select.querySelectorAll('.vs__selected') : [];
        if (selected.length !== 1)
            return {error: 'ambiguous_duration'};
        return {
            content: description.value,
            editor_content: editor.innerText,
            price: priceInputs[0].value.replace(/\\s/g, ''),
            duration: (selected[0].textContent || '').trim(),
        };
    }""",
        {"expectedContent": content, "offerPrice": offer_price},
    )
    if isinstance(actual, dict) and actual.get("error"):
        raise RuntimeError("Поля формы отсутствуют или неоднозначны: " + actual["error"])
    if not isinstance(actual, dict) or actual.get("content") != content:
        raise RuntimeError("Текст формы не совпадает с одобренным")
    editor_text = (actual.get("editor_content") or "").replace("\r\n", "\n")
    if editor_text != content.replace("\r\n", "\n"):
        raise RuntimeError("Видимый текст формы не совпадает с одобренным")
    if actual.get("price") != str(offer_price):
        raise RuntimeError("Цена формы не совпадает с одобренной")
    if not isinstance(actual.get("duration"), str) or not re.fullmatch(
        rf"{duration_days}\s*(?:день|дня|дней|day|days)?", actual["duration"].strip(), re.IGNORECASE
    ):
        raise RuntimeError("Срок формы не совпадает с одобренным")


async def _fill_offer_form(
    page: Page,
    content: str,
    budget_min: float | None,
    budget_max: float | None,
    *,
    offer_price: int | None = None,
    duration_days: int | None = None,
    offer_title: str | None = None,
) -> None:
    validate_approved_submission(content, offer_price, duration_days)

    await dismiss_overlays(page)
    if await page.locator(UNAPPROVED_OPTIONAL_CONTROLS).count():
        raise RuntimeError("Форма содержит неподтверждённое название или способ оплаты")
    await _fill_trumbowyg(page, "решать задачу", content)

    price = offer_price

    filled, _method = await _fill_price_input(page, price)
    if not filled:
        raise RuntimeError("Поле стоимости не найдено на форме Kwork")

    await _select_duration(page, duration_days)

async def _select_duration(page: Page, duration_days: int) -> None:
    duration_input = page.locator('input[placeholder="Срок выполнения"]')
    if await duration_input.count() == 0:
        raise RuntimeError("Поле срока не найдено")
    await duration_input.click(force=True)
    await page.wait_for_timeout(500)
    options = page.locator('.vs__dropdown-option, [role="option"]')
    for index in range(await options.count()):
        option = options.nth(index)
        label = (await option.inner_text()).strip()
        if re.fullmatch(rf"{duration_days}\s*(?:день|дня|дней|day|days)?", label, re.IGNORECASE):
            await option.click()
            return
    raise RuntimeError("Одобренный срок отсутствует в форме")


async def _fail_offer(
    page: Page,
    label: str,
    error: str,
    *,
    url: str | None = None,
    job_url: str | None = None,
    content: str = "",
    steps: list[str] | None = None,
    **log_kwargs: Any,
) -> tuple[bool, str | None, dict[str, Any]]:
    step_list = list(steps or [])
    step_list.append(f"ошибка: {error}")
    content_file = _save_submission_content(content, label) if content else None
    screenshot = await _save_debug_screenshot(page, label)
    debug_file = await _save_debug_context(
        label,
        job_url=job_url or url or page.url,
        content=content,
        steps=step_list,
        error=error,
        screenshot=screenshot,
        content_file=content_file,
        url=url or page.url,
        **log_kwargs,
    )
    logger.error(
        "Kwork offer step failed",
        error=error,
        screenshot=screenshot,
        debug_file=debug_file,
        content_file=content_file,
        url=url or page.url,
        content_length=len(content),
        steps=step_list,
        **log_kwargs,
    )
    debug = {
        "job_url": job_url or url or page.url,
        "content_length": len(content),
        "content_file": content_file,
        "steps": step_list,
        "error_label": label,
        "screenshot": screenshot,
        "debug_file": debug_file,
        **log_kwargs,
    }
    if label == "connects_limit_exhausted" or is_kwork_connects_limit_error(error):
        record_connects_limit_detected(error, source=f"offer:{label}")
    return False, error, debug


async def submit_offer(
    page: Page,
    job_url: str,
    content: str,
    budget_min: float | None = None,
    budget_max: float | None = None,
    *,
    offer_price: int | None = None,
    duration_days: int | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    steps: list[str] = []
    project_id = _extract_project_id(job_url)
    offer_url = _offer_form_url(job_url)
    if not offer_url or not project_id:
        content_file = _save_submission_content(content, "no_project_id")
        logger.error(
            "Kwork project id not found in url",
            url=job_url,
            content_file=content_file,
            content_length=len(content),
        )
        return (
            False,
            "Не удалось определить ID проекта Kwork",
            {
                "job_url": job_url,
                "content_length": len(content),
                "content_file": content_file,
                "steps": ["не удалось извлечь ID проекта из URL"],
            },
        )

    try:
        validate_approved_submission(content, offer_price, duration_days)
    except ValueError as exc:
        return False, str(exc), {"error_label": "invalid_approved_payload"}

    steps.append(
        f"подготовка: текст {len(content)} симв. (рекомендуется ≤1200, лимит Kwork {KWORK_MAX_OFFER_CHARS})"
    )
    if offer_price:
        steps.append(f"подготовка: цена отклика {offer_price} ₽")

    content_file = _save_submission_content(content, f"submit_{project_id}")
    logger.info(
        "Kwork opening offer form",
        job_url=job_url,
        offer_url=offer_url,
        project_id=project_id,
        content_length=len(content),
        content_file=content_file,
        offer_price=offer_price,
    )

    steps.append(f"открытие формы: {offer_url}")
    await page.goto(offer_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    try:
        await page.wait_for_load_state("networkidle", timeout=60000)
    except Exception:
        pass
    steps.append(f"страница загружена: {page.url}")

    if await _is_access_blocked(page):
        return await _fail_offer(
            page,
            "access_blocked",
            "Kwork заблокировал доступ с этого IP",
            url=offer_url,
            job_url=job_url,
            content=content,
            steps=steps,
        )

    if "/login" in page.url:
        return await _fail_offer(
            page,
            "session_expired",
            "Сессия Kwork устарела — пересохраните data/kwork_session.json",
            url=page.url,
            job_url=job_url,
            content=content,
            steps=steps,
        )

    if "new_offer" not in page.url:
        steps.append("форма new_offer недоступна, открытие отклика со страницы заказа")
        if await _detect_connects_limit_on_page(page):
            return await _fail_offer(
                page,
                "connects_limit_exhausted",
                await _connects_limit_error_from_page(page),
                url=offer_url,
                job_url=job_url,
                content=content,
                steps=steps + ["лимит коннектов обнаружен до открытия формы"],
            )
        if await _open_new_offer_from_project_page(page, job_url, steps):
            steps.append("форма отклика открыта через «Предложить услугу»")
        elif any("блокировка Kwork:" in step for step in steps):
            blocker = next(
                step.removeprefix("блокировка Kwork: ")
                for step in reversed(steps)
                if step.startswith("блокировка Kwork:")
            )
            return await _fail_offer(
                page,
                "offer_blocked",
                blocker,
                url=offer_url,
                job_url=job_url,
                content=content,
                steps=steps,
                reason=blocker,
            )
        elif await _verify_offer_on_project_page(
            page, project_id
        ) or await _verify_offer_in_my_offers(page, project_id):
            return (
                False,
                "Существующий отклик требует отдельного одобрения редактирования",
                {"error_label": "existing_offer_requires_approval"},
            )
        else:
            steps.append("диагностика блокировки на странице проекта")
            blocker = await _diagnose_offer_blocker(page, job_url)
            if blocker:
                return await _fail_offer(
                    page,
                    "offer_blocked",
                    blocker,
                    url=offer_url,
                    job_url=job_url,
                    content=content,
                    steps=steps,
                    reason=blocker,
                )
            return await _fail_offer(
                page,
                "form_unavailable",
                "Форма отклика недоступна на Kwork",
                url=offer_url,
                job_url=job_url,
                content=content,
                steps=steps,
                redirect=page.url,
            )

    steps.append("форма отклика открыта")
    ready, ready_error = await _check_seller_ready(page)
    if not ready:
        return await _fail_offer(
            page,
            "seller_not_ready",
            ready_error or "Профиль продавца не готов к откликам",
            url=offer_url,
            job_url=job_url,
            content=content,
            steps=steps,
        )
    steps.append("профиль продавца готов")

    if await _exchange_lesson_required(page):
        return await _fail_offer(
            page,
            "exchange_lesson_required",
            "Пройдите урок по работе на Бирже на Kwork",
            url=offer_url,
            job_url=job_url,
            content=content,
            steps=steps,
        )
    steps.append("урок по Бирже не требуется")

    textarea = page.locator('textarea[name="description"]')
    try:
        # Trumbowyg stores submitted text in a hidden textarea.
        await textarea.wait_for(state="attached", timeout=60000)
        await page.locator('.trumbowyg-editor[placeholder*="решать задачу"]').wait_for(
            state="visible", timeout=60000
        )
    except Exception:
        form_debug = {
            "title": await page.title(),
            "description_count": await textarea.count(),
            "submit_count": await page.get_by_role("button", name="Предложить").count(),
        }
        return await _fail_offer(
            page,
            "form_not_loaded",
            "Форма отклика не загрузилась",
            url=offer_url,
            job_url=job_url,
            content=content,
            steps=steps,
            current_url=page.url,
            **form_debug,
        )

    steps.append("заполнение полей формы")
    offer_title = None
    for selector in ("h1", ".page-title", ".wants-card__header-title"):
        heading = page.locator(selector).first
        if await heading.count():
            offer_title = ((await heading.text_content()) or "").strip()[:80] or None
            if offer_title:
                break
    try:
        await _fill_offer_form(
            page,
            content,
            budget_min,
            budget_max,
            offer_price=offer_price,
            duration_days=duration_days,
            offer_title=offer_title,
        )
    except Exception as exc:
        return await _fail_offer(
            page,
            "form_fill_error",
            f"Не удалось заполнить форму: {exc}",
            url=offer_url,
            job_url=job_url,
            content=content,
            steps=steps,
            offer_price=offer_price,
        )
    await _save_debug_screenshot(page, f"before_submit_{project_id}")
    steps.append("форма заполнена, скриншот before_submit сохранён")

    try:
        await _verify_approved_form(page, content, offer_price, duration_days)
    except RuntimeError as exc:
        return False, str(exc), {"error_label": "approved_form_mismatch"}
    clicked = await _click_submit_offer_button(page)
    if not clicked:
        return await _fail_offer(
            page,
            "submit_button_missing",
            "Кнопка «Предложить» не найдена на форме",
            url=offer_url,
            job_url=job_url,
            content=content,
            steps=steps,
        )

    steps.append(f"нажатие «{clicked}»")
    await page.wait_for_timeout(5000)
    steps.append(f"ожидание ответа, текущий URL: {page.url}")

    if "new_offer" in page.url:
        errors = await _collect_visible_form_errors(page)
        body = (await page.inner_text("body")).lower()
        if errors:
            steps.append(f"ошибки формы: {', '.join(errors)}")
            return await _fail_offer(
                page,
                "form_validation_error",
                errors[0],
                url=offer_url,
                job_url=job_url,
                content=content,
                steps=steps,
                errors=errors,
                offer_price=offer_price,
            )
        if await _exchange_lesson_required(page):
            return await _fail_offer(
                page,
                "exchange_lesson_after_submit",
                "Пройдите урок по работе на Бирже на Kwork",
                url=offer_url,
                job_url=job_url,
                content=content,
                steps=steps,
            )
        if "не подтвержден" in body or "онбординг" in body:
            return await _fail_offer(
                page,
                "onboarding_required_after_submit",
                "Профиль продавца не подтверждён на Kwork",
                url=offer_url,
                job_url=job_url,
                content=content,
                steps=steps,
            )
        if connects_limit_exhausted(body):
            return await _fail_offer(
                page,
                "connects_limit_exhausted",
                await _connects_limit_error_from_page(page),
                url=offer_url,
                job_url=job_url,
                content=content,
                steps=steps,
                current_url=page.url,
                offer_price=offer_price,
            )
        return await _fail_offer(
            page,
            "stayed_on_form",
            "Kwork не принял отклик — форма осталась открытой",
            url=offer_url,
            job_url=job_url,
            content=content,
            steps=steps,
            current_url=page.url,
            offer_price=offer_price,
        )

    if await _verify_offer_after_submit_redirect(page, project_id):
        logger.info("Kwork offer verified after submit redirect", project_id=project_id)
        return await _offer_submit_success(
            page,
            job_url=job_url,
            content=content,
            content_file=content_file,
            steps=steps,
            offer_price=offer_price,
        )

    if await _verify_offer_on_project_page(page, project_id):
        logger.info("Kwork offer verified on project page after submit", project_id=project_id)
        return await _offer_submit_success(
            page,
            job_url=job_url,
            content=content,
            content_file=content_file,
            steps=steps,
            offer_price=offer_price,
        )

    if await _verify_offer_in_my_offers(page, project_id):
        logger.info("Kwork offer verified in my offers", project_id=project_id)
        return await _offer_submit_success(
            page,
            job_url=job_url,
            content=content,
            content_file=content_file,
            steps=steps,
            offer_price=offer_price,
        )

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
        return await _offer_submit_success(
            page,
            job_url=job_url,
            content=content,
            content_file=content_file,
            steps=steps,
            offer_price=offer_price,
        )

    screenshot = await _save_debug_screenshot(page, f"unverified_submit_{project_id}")
    debug_file = await _save_debug_context(
        "unverified_submit",
        job_url=job_url,
        content=content,
        steps=steps + ["отклик не найден в «Мои отклики»"],
        error="Отклик не появился в «Мои отклики» — отправка не подтверждена",
        screenshot=screenshot,
        content_file=content_file,
        offer_price=offer_price,
    )
    logger.warning(
        "Kwork offer not found in my offers after submit",
        url=offer_url,
        current_url=page.url,
        screenshot=screenshot,
        debug_file=debug_file,
        content_file=content_file,
    )
    return (
        False,
        "Отклик не появился в «Мои отклики» — отправка не подтверждена",
        {
            "job_url": job_url,
            "content_length": len(content),
            "content_file": content_file,
            "steps": steps + ["отклик не найден в «Мои отклики»"],
            "error_label": "unverified_submit",
            "screenshot": screenshot,
            "debug_file": debug_file,
            "offer_price": offer_price,
        },
    )


async def send_kwork_offer(
    job_url: str,
    content: str,
    budget_min: float | None = None,
    budget_max: float | None = None,
    *,
    offer_price: int | None = None,
    duration_days: int | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    async with _kwork_send_lock:
        try:
            validate_approved_submission(content, offer_price, duration_days)
        except ValueError as exc:
            return False, str(exc), {"error_label": "invalid_approved_payload"}
        return await _send_kwork_offer_locked(
            job_url,
            content,
            budget_min,
            budget_max,
            offer_price=offer_price,
            duration_days=duration_days,
        )


async def _send_kwork_offer_locked(
    job_url: str,
    content: str,
    budget_min: float | None = None,
    budget_max: float | None = None,
    *,
    offer_price: int | None = None,
    duration_days: int | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    from app.utils.proxy import get_proxy_candidates, mark_proxy_failed, mask_proxy

    settings = get_settings()
    last_result: tuple[bool, str | None, dict[str, Any] | None] = (
        False,
        "Не удалось войти в Kwork — проверьте сессию",
        None,
    )

    candidates = list(get_proxy_candidates(settings, "openai"))
    if None not in candidates:
        candidates.insert(0, None)

    for proxy in candidates:
        playwright: Playwright | None = None
        browser: Browser | None = None
        submit_started = False
        steps = ["запуск браузера"]
        label = mask_proxy(proxy) if proxy else "direct"
        steps.append(f"прокси: {label}")

        try:
            playwright, browser, page = await launch_browser(proxy=proxy)
            steps.append("браузер запущен")
            logged_in = await login(page, settings.kwork_email, settings.kwork_password)
            if not logged_in:
                content_file = _save_submission_content(content, "login_failed")
                screenshot = await _save_debug_screenshot(page, "login_failed")
                debug_file = await _save_debug_context(
                    "login_failed",
                    job_url=job_url,
                    content=content,
                    steps=steps + ["вход в Kwork не удался"],
                    error="Не удалось войти в Kwork — проверьте сессию",
                    screenshot=screenshot,
                    content_file=content_file,
                )
                last_result = (
                    False,
                    "Не удалось войти в Kwork — проверьте сессию",
                    {
                        "job_url": job_url,
                        "content_length": len(content),
                        "content_file": content_file,
                        "steps": steps + ["вход в Kwork не удался"],
                        "error_label": "login_failed",
                        "screenshot": screenshot,
                        "debug_file": debug_file,
                    },
                )
                if proxy:
                    mark_proxy_failed(settings, "openai", proxy)
                continue

            steps.append("вход в Kwork успешен")
            logger.info(
                "Kwork login ok, submitting offer",
                job_url=job_url,
                proxy=label,
                storage_state=settings.kwork_storage_state or None,
            )
            submit_started = True
            success, error, debug = await submit_offer(
                page,
                job_url,
                content,
                budget_min,
                budget_max,
                offer_price=offer_price,
                duration_days=duration_days,
            )
            if debug and "steps" in debug:
                if not success and any(
                    str(step).startswith("нажатие «") for step in debug["steps"]
                ):
                    debug["ambiguous"] = True
                debug["steps"] = steps + debug["steps"]
            elif not success:
                content_file = _save_submission_content(content, "submit_failed")
                debug = {
                    "job_url": job_url,
                    "content_length": len(content),
                    "content_file": content_file,
                    "steps": steps,
                    "error_label": "submit_failed",
                    **(debug or {}),
                }
            if success and settings.kwork_storage_state.strip():
                output = Path(settings.kwork_storage_state)
                try:
                    await save_browser_storage_state(page.context, output)
                except Exception as exc:
                    logger.warning("Kwork session refresh failed", error=str(exc))
            return success, error, debug
        except Exception as exc:
            content_file = _save_submission_content(content, "exception")
            logger.error(
                "Kwork offer send failed",
                error=str(exc),
                url=job_url,
                proxy=label,
                exc_type=type(exc).__name__,
                content_file=content_file,
                content_length=len(content),
            )
            debug_file = await _save_debug_context(
                "exception",
                job_url=job_url,
                content=content,
                steps=steps + [f"исключение: {type(exc).__name__}"],
                error=str(exc),
                content_file=content_file,
            )
            last_result = (
                False,
                f"Ошибка отправки: {exc}",
                {
                    "job_url": job_url,
                    "content_length": len(content),
                    "content_file": content_file,
                    "steps": steps + [f"исключение: {type(exc).__name__}: {exc}"],
                    "error_label": "exception",
                    "debug_file": debug_file,
                },
            )
            if submit_started:
                last_result[2]["ambiguous"] = True
                return last_result
            if proxy:
                mark_proxy_failed(settings, "openai", proxy)
        finally:
            if playwright and browser:
                await close_browser(playwright, browser)

    return last_result


async def verify_kwork_offer_submitted(job_url: str) -> bool:
    """Read-only check whether our offer appears submitted for the project (reconciliation)."""
    from app.utils.proxy import get_proxy_candidates, mark_proxy_failed, mask_proxy

    settings = get_settings()
    project_id = extract_site_order_id(url=job_url, platform="kwork")
    if not project_id:
        return False

    candidates = list(get_proxy_candidates(settings, "openai"))
    if None not in candidates:
        candidates.insert(0, None)

    for proxy in candidates:
        playwright: Playwright | None = None
        browser: Browser | None = None
        label = mask_proxy(proxy) if proxy else "direct"
        try:
            playwright, browser, page = await launch_browser(proxy=proxy)
            logged_in = await login(page, settings.kwork_email, settings.kwork_password)
            if not logged_in:
                if proxy:
                    mark_proxy_failed(settings, "openai", proxy)
                continue
            if await _verify_offer_on_project_page(page, project_id):
                return True
        except Exception as exc:
            logger.warning(
                "Kwork offer verify failed",
                job_url=job_url,
                proxy=label,
                error=str(exc),
            )
            if proxy:
                mark_proxy_failed(settings, "openai", proxy)
        finally:
            if playwright and browser:
                await close_browser(playwright, browser)
    return False


async def probe_kwork_connects_status(page: Page) -> dict[str, Any]:
    settings = get_settings()

    listing_url = settings.kwork_category_url or "https://kwork.ru/projects?a=1"
    await page.goto(listing_url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2500)
    if await _is_access_blocked(page) or "/login" in page.url:
        return {"error": "session_or_access", "limit_exhausted": False}

    page_text = await _visible_page_texts(page)
    fields = connects_balance_fields(page_text)
    if fields.get("connects_remaining", 0) > 0:
        return {
            "limit_exhausted": False,
            "confident": True,
            "connects_available_confirmed": True,
            "page_text": page_text,
            "project_url": listing_url,
            **fields,
        }
    if connects_limit_exhausted(page_text):
        return {
            "limit_exhausted": True,
            "confident": True,
            "page_text": page_text,
            "project_url": listing_url,
            **fields,
        }

    for connects_url in (
        "https://kwork.ru/connects",
        "https://kwork.ru/balance/connects",
    ):
        await page.goto(connects_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        if "/login" in page.url or await _is_access_blocked(page):
            break
        page_text = await _visible_page_texts(page)
        fields = connects_balance_fields(page_text)
        if fields.get("connects_remaining", 0) > 0:
            return {
                "limit_exhausted": False,
                "confident": True,
                "connects_available_confirmed": True,
                "page_text": page_text,
                "project_url": connects_url,
                **fields,
            }
        if connects_limit_exhausted(page_text):
            return {
                "limit_exhausted": True,
                "confident": True,
                "page_text": page_text,
                "project_url": connects_url,
                **fields,
            }
        if _connects_balance_available(page_text):
            return {
                "limit_exhausted": False,
                "confident": True,
                "connects_available_confirmed": True,
                "page_text": page_text,
                "project_url": connects_url,
            }

    project_urls: list[str] = []
    links = page.locator('a[href*="/projects/"]')
    link_count = await links.count()
    for index in range(min(link_count, 20)):
        href = await links.nth(index).get_attribute("href")
        if not href or not re.search(r"/projects/\d+", href):
            continue
        project_url = href if href.startswith("http") else f"https://kwork.ru{href}"
        project_url = re.sub(r"(/projects/\d+)(?:/.*)?$", r"\1/view", project_url.split("?")[0])
        if project_url not in project_urls:
            project_urls.append(project_url)
        if len(project_urls) >= 3:
            break

    if not project_urls:
        return {"error": "no_project_link", "limit_exhausted": False, "confident": False}

    last_text = ""
    for project_url in project_urls:
        await page.goto(project_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3500)
        page_text = await _visible_page_texts(page)
        last_text = page_text
        if await _detect_connects_limit_on_page(page):
            return {
                "limit_exhausted": True,
                "confident": True,
                "page_text": page_text,
                "project_url": project_url,
            }

        for locator in _propose_offer_locators(page):
            if await locator.count() == 0:
                continue
            button = locator.first
            if not await button.is_visible():
                continue
            if await button.is_disabled():
                body_lower = page_text.lower()
                if _order_closed_in_body(body_lower):
                    continue
                if await _detect_connects_limit_on_page(page):
                    return {
                        "limit_exhausted": True,
                        "confident": True,
                        "page_text": page_text,
                        "project_url": project_url,
                    }
                return {
                    "limit_exhausted": True,
                    "confident": True,
                    "page_text": page_text,
                    "project_url": project_url,
                }
            try:
                await button.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            try:
                await button.click(force=True)
                await page.wait_for_timeout(2500)
                page_text = await _visible_page_texts(page)
                last_text = page_text
            except Exception:
                pass
            if await _detect_connects_limit_on_page(page):
                return {
                    "limit_exhausted": True,
                    "confident": True,
                    "page_text": page_text,
                    "project_url": project_url,
                }
            if await _offer_form_visible(page):
                return {
                    "limit_exhausted": False,
                    "confident": False,
                    "page_text": page_text,
                    "project_url": project_url,
                }
            return {
                "limit_exhausted": False,
                "confident": False,
                "page_text": page_text,
                "project_url": project_url,
            }

    return {
        "limit_exhausted": False,
        "confident": False,
        "page_text": last_text,
        "project_url": project_urls[-1],
    }


async def refresh_kwork_pause_from_kwork(*, force: bool = False) -> dict[str, Any]:
    from app.services.kwork_pause import apply_connects_probe_result, should_refresh_pause_state
    from app.utils.proxy import get_proxy_candidates, mask_proxy

    settings = get_settings()
    if not settings.kwork_pause_enabled or not settings.kwork_pause_auto:
        return {"skipped": True, "reason": "auto_pause_disabled"}
    if not force and not should_refresh_pause_state():
        return {"skipped": True, "reason": "throttled"}

    proxies = get_proxy_candidates(settings, "openai")
    last_error: str | None = None
    for proxy in proxies:
        playwright = browser = None
        try:
            label = mask_proxy(proxy) if proxy else "direct"
            logger.info("JobPilot AI checking Kwork connects pause", proxy=label, force=force)
            playwright, browser, page = await launch_browser(proxy=proxy)
            probe = await probe_kwork_connects_status(page)
            if probe.get("error"):
                last_error = str(probe["error"])
                logger.warning("Kwork connects probe failed", error=last_error, proxy=label)
                continue
            return apply_connects_probe_result(
                limit_exhausted=bool(probe.get("limit_exhausted")),
                page_text=str(probe.get("page_text", "")),
                source="browser_probe",
                confident=bool(probe.get("confident", True)),
                connects_available_confirmed=bool(probe.get("connects_available_confirmed")),
            )
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Kwork connects probe exception", error=last_error, proxy=label)
        finally:
            if playwright and browser:
                await close_browser(playwright, browser)

    return {"skipped": True, "error": last_error}
