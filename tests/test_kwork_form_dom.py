"""Run actual adapter JavaScript against synthetic offline HTML, never Kwork.

Opt in with JOBPILOT_BROWSER_TESTS=1. Missing/blocked Chromium is a failure when
opted in; default unit runs explicitly skip these browser-dependent checks.
"""

import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from app.services.kwork_browser import (
    _click_submit_offer_button,
    _fill_offer_form,
    _select_duration,
    _verify_approved_form,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_BROWSER_TESTS") != "1",
    reason="explicit offline Chromium opt-in required",
)
FIXTURE = Path(__file__).parent / "fixtures/kwork/offer_form_synthetic.html"
CONTENT = "  **<b>Точный текст & тире —</b>**\n\n" + "Решение с тестами 😀. " * 9 + "\nКонец  "


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, executable_path=os.environ.get("JOBPILOT_TEST_CHROMIUM") or None
        )
        context = await browser.new_context(service_workers="block", accept_downloads=False)
        await context.route("**/*", lambda route: route.abort())
        page = await context.new_page()
        await page.set_content(FIXTURE.read_text())
        page.set_default_timeout(3000)
        try:
            yield page
        finally:
            await context.close()
            await browser.close()


async def set_approved_values(page):
    await page.evaluate(
        """content => {
        document.querySelector('textarea[name="description"]').value = content;
        document.querySelector('.trumbowyg-editor').textContent = content;
        document.querySelector('input[name="price"]').value = '8500';
        document.querySelector('.vs__selected').textContent = '7 дней';
    }""",
        CONTENT,
    )


async def test_full_fill_hidden_textarea_and_exact_local_submission(page):
    await _fill_offer_form(page, CONTENT, 30000, 50000, offer_price=8500, duration_days=7)
    await _verify_approved_form(page, CONTENT, 8500, 7)
    assert await _click_submit_offer_button(page) == "Предложить"
    assert await page.evaluate("window.lastSubmitted") == {
        "content": CONTENT,
        "price": "8500",
        "duration": "7 дней",
    }
    assert await page.evaluate("window.publishedChanges") == ["input", "change"]


async def test_search_query_seven_is_not_selected_duration(page):
    await set_approved_values(page)
    await page.locator(".vs__selected").evaluate("element => element.textContent = '17 дней'")
    await page.locator('input[placeholder="Срок выполнения"]').fill("7")
    with pytest.raises(RuntimeError):
        await _verify_approved_form(page, CONTENT, 8500, 7)
    assert await page.evaluate("window.submitCount") == 0


async def test_changed_visible_editor_cannot_hide_behind_correct_textarea(page):
    await set_approved_values(page)
    await page.locator(".trumbowyg-editor").fill("Changed visible text")
    with pytest.raises(RuntimeError):
        await _verify_approved_form(page, CONTENT, 8500, 7)


@pytest.mark.parametrize("field", ["description", "price", "duration"])
async def test_duplicate_controls_fail_closed(page, field):
    await set_approved_values(page)
    await page.evaluate(
        """field => {
        const selector = {description: 'textarea[name="description"]', price: 'input[name="price"]',
                          duration: 'input[placeholder="Срок выполнения"]'}[field];
        const element = document.querySelector(selector);
        element.parentNode.appendChild(element.cloneNode(true));
    }""",
        field,
    )
    with pytest.raises(RuntimeError):
        await _verify_approved_form(page, CONTENT, 8500, 7)


async def test_named_price_alias_is_supported(page):
    await set_approved_values(page)
    await page.locator('input[name="price"]').evaluate("element => element.name = 'kwork_price'")
    await _verify_approved_form(page, CONTENT, 8500, 7)


@pytest.mark.parametrize("field", ["description", "price", "duration"])
async def test_missing_control_fail_closed(page, field):
    await set_approved_values(page)
    await page.evaluate(
        """field => {
        const selector = {description: 'textarea[name="description"]', price: 'input[name="price"]',
                          duration: 'input[placeholder="Срок выполнения"]'}[field];
        document.querySelector(selector).remove();
    }""",
        field,
    )
    with pytest.raises(RuntimeError):
        await _verify_approved_form(page, CONTENT, 8500, 7)


async def test_only_seventeen_days_never_becomes_seven(page):
    await page.locator('[data-days="7"]').evaluate("element => element.remove()")
    with pytest.raises(RuntimeError, match="отсутствует"):
        await _select_duration(page, 7)
    assert await page.locator(".vs__selected").inner_text() == "1 день"


async def test_client_side_price_clamp_is_detected_before_submit(page):
    await page.locator('input[name="price"]').evaluate("""element => {
        element.addEventListener('input', () => {element.value = '9000'});
    }""")
    await _fill_offer_form(page, CONTENT, 30000, 50000, offer_price=8500, duration_days=7)
    with pytest.raises(RuntimeError):
        await _verify_approved_form(page, CONTENT, 8500, 7)
    assert await page.evaluate("window.submitCount") == 0


async def test_controls_from_another_form_fail_closed(page):
    await set_approved_values(page)
    await page.evaluate("""() => {
        const other = document.createElement('form');
        document.body.append(other);
        other.append(document.querySelector('input[name="price"]'));
    }""")
    with pytest.raises(RuntimeError, match="different_forms"):
        await _verify_approved_form(page, CONTENT, 8500, 7)


async def test_duplicate_selected_duration_fail_closed(page):
    await set_approved_values(page)
    await page.locator(".vs__selected").evaluate(
        "element => element.after(element.cloneNode(true))"
    )
    with pytest.raises(RuntimeError, match="ambiguous_duration"):
        await _verify_approved_form(page, CONTENT, 8500, 7)


async def test_hidden_editor_fail_closed(page):
    await set_approved_values(page)
    await page.locator(".trumbowyg-editor").evaluate("element => element.style.display = 'none'")
    with pytest.raises(RuntimeError, match="editor_not_visible"):
        await _verify_approved_form(page, CONTENT, 8500, 7)


@pytest.mark.parametrize("control", ["payment", "title"])
async def test_unapproved_optional_fields_are_not_changed_or_submitted(page, control):
    await page.evaluate("""control => {
        if (control === 'payment') {
            const input = document.createElement('input');
            input.name = 'payment_type';
            input.type = 'hidden';
            document.querySelector('form').append(input);
            return;
        }
        const extra = document.createElement('div');
        extra.className = 'trumbowyg-editor';
        extra.setAttribute('placeholder', 'Введите название заказа');
        extra.contentEditable = 'true';
        extra.textContent = 'Original title';
        document.querySelector('form').append(extra);
    }""", control)
    with pytest.raises(RuntimeError):
        await _fill_offer_form(page, CONTENT, 30000, 50000, offer_price=8500, duration_days=7)
    assert not await page.evaluate('window.paymentClicked || false')
    assert await page.evaluate('window.submitCount') == 0
    if control == "title":
        assert await page.locator('.trumbowyg-editor[placeholder*="название"]').inner_text() == "Original title"


@pytest.mark.parametrize("control", ["payment", "title"])
async def test_optional_fields_added_after_fill_fail_readback(page, control):
    await set_approved_values(page)
    await page.evaluate("""control => {
        const extra = document.createElement(control === 'payment' ? 'input' : 'div');
        if (control === 'payment') {
            extra.name = 'payment_type';
            extra.type = 'hidden';
        } else {
            extra.className = 'trumbowyg-editor';
            if (control === 'title') extra.setAttribute('placeholder', 'Введите название заказа');
        }
        document.querySelector('form').append(extra);
    }""", control)
    with pytest.raises(RuntimeError):
        await _verify_approved_form(page, CONTENT, 8500, 7)


@pytest.mark.parametrize("name", ["payment_type", "paymentType", "title", "kwork_title"])
async def test_named_unapproved_inputs_fail_readback(page, name):
    await set_approved_values(page)
    await page.evaluate("""name => {
        const input = document.createElement('input');
        input.name = name;
        input.type = 'hidden';
        document.querySelector('form').append(input);
    }""", name)
    with pytest.raises(RuntimeError, match="unapproved_optional_control"):
        await _verify_approved_form(page, CONTENT, 8500, 7)


@pytest.mark.parametrize("mismatch", [None, "price", "payment", "title"])
async def test_production_submit_path_obeys_dom_guard(page, monkeypatch, mismatch):
    """Run real fill/readback/click through submit_offer, intercept all navigation locally."""
    from unittest.mock import AsyncMock

    from app.services import kwork_browser

    url = "https://fixture.invalid/new_offer?project=123"
    await page.route(
        "https://fixture.invalid/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=FIXTURE.read_text()),
    )
    monkeypatch.setattr(kwork_browser, "_offer_form_url", lambda _: url)
    monkeypatch.setattr(kwork_browser, "_check_seller_ready", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(kwork_browser, "_exchange_lesson_required", AsyncMock(return_value=False))
    monkeypatch.setattr(kwork_browser, "_save_submission_content", lambda *args: None)
    monkeypatch.setattr(page, "wait_for_timeout", AsyncMock())

    async def screenshot(*args):
        if mismatch == "price":
            await page.locator('input[name="price"]').fill("9000")
        elif mismatch in ("payment", "title"):
            await page.evaluate("""control => {
                const input = document.createElement('input');
                input.name = control === 'payment' ? 'payment_type' : 'title';
                input.type = 'hidden';
                document.querySelector('form').append(input);
            }""", mismatch)

    monkeypatch.setattr(kwork_browser, "_save_debug_screenshot", screenshot)

    async def failure(_page, label, error, **kwargs):
        return False, error, {"error_label": label}

    monkeypatch.setattr(kwork_browser, "_fail_offer", failure)
    ok, _, debug = await kwork_browser.submit_offer(
        page, "https://fixture.invalid/projects/123", CONTENT, offer_price=8500, duration_days=7
    )
    assert not ok  # Local HTML has no marketplace receipt; never a live delivery assertion.
    assert await page.evaluate("window.submitCount") == (0 if mismatch else 1)
    assert debug["error_label"] == ("approved_form_mismatch" if mismatch else "stayed_on_form")


async def test_submit_button_must_be_unique_in_approved_form(page):
    await set_approved_values(page)
    await page.evaluate("document.querySelector('form').append(document.querySelector('button').cloneNode(true))")
    assert await _click_submit_offer_button(page) is None
    assert await page.evaluate('window.submitCount') == 0


async def test_submit_ignores_button_from_unrelated_form(page):
    await set_approved_values(page)
    await page.evaluate("""() => {
        const other = document.createElement('form');
        other.innerHTML = '<button>Предложить</button>';
        document.body.prepend(other);
    }""")
    assert await _click_submit_offer_button(page) == 'Предложить'
    assert await page.evaluate('window.submitCount') == 1


async def test_hidden_price_cannot_authorize_submission(page):
    await set_approved_values(page)
    await page.locator('input[name="price"]').evaluate("el => el.type = 'hidden'")
    with pytest.raises(RuntimeError):
        await _verify_approved_form(page, CONTENT, 8500, 7)
