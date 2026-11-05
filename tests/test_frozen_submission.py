from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.kwork_browser import (
    _select_duration,
    _verify_approved_form,
    validate_approved_submission,
)


@pytest.mark.parametrize(
    "text,price,days",
    [
        ("short", 500, 7),
        ("x" * 2001, 500, 7),
        ("x" * 150, None, 7),
        ("x" * 150, True, 7),
        ("x" * 150, 500, None),
    ],
)
def test_invalid_approved_payload_is_rejected(text, price, days):
    with pytest.raises(ValueError):
        validate_approved_submission(text, price, days)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("content", "different"), ("price", "5000"), ("duration", "17 дней")]
)
async def test_readback_rejects_changed_form(field, value):
    actual = {
        "content": "x" * 150,
        "editor_content": "x" * 150,
        "price": "8500",
        "duration": "7 дней",
    }
    actual[field] = value
    with pytest.raises(RuntimeError):
        await _verify_approved_form(
            SimpleNamespace(evaluate=AsyncMock(return_value=actual)), "x" * 150, 8500, 7
        )


@pytest.mark.asyncio
async def test_readback_accepts_exact_payload():
    actual = {
        "content": "x" * 150,
        "editor_content": "x" * 150,
        "price": "8500",
        "duration": "7 дней",
    }
    await _verify_approved_form(
        SimpleNamespace(evaluate=AsyncMock(return_value=actual)), "x" * 150, 8500, 7
    )


@pytest.mark.asyncio
async def test_duration_does_not_select_17_as_7_or_first_fallback():
    option = SimpleNamespace(inner_text=AsyncMock(return_value="17 дней"), click=AsyncMock())
    options = SimpleNamespace(count=AsyncMock(return_value=1), nth=lambda _: option)
    input_field = SimpleNamespace(count=AsyncMock(return_value=1), click=AsyncMock())

    def _locator(selector: str):
        if selector.startswith("input"):
            return SimpleNamespace(first=input_field)
        return options

    page = SimpleNamespace(locator=_locator, wait_for_timeout=AsyncMock())
    with pytest.raises(RuntimeError, match="отсутствует"):
        await _select_duration(page, 7)
    option.click.assert_not_awaited()


@pytest.mark.asyncio
async def test_sender_passes_frozen_parameters_and_text_without_padding(monkeypatch):
    from app.config import Settings
    from app.services import proposal_sender

    monkeypatch.setattr(
        proposal_sender, "get_settings", lambda: Settings(_env_file=None, PLATFORM_DRY_RUN=False)
    )
    monkeypatch.setattr(proposal_sender, "is_kwork_paused", lambda: False)
    send = AsyncMock(return_value=(True, None, {}))
    monkeypatch.setattr(proposal_sender, "send_kwork_offer", send)
    job = SimpleNamespace(
        platform="kwork",
        title="Fake",
        url="https://example.test/project",
        budget_min=10000,
        budget_max=20000,
    )
    payload = {
        "content": "Exact approved text",
        "offer_price": 8500,
        "duration_days": 7,
        "offer_title": "Test title",
        "payment_type_label": "Безопасная",
    }
    await proposal_sender.ProposalSender()._send_to_platform(
        job, None, payload["content"], approved_payload=payload
    )
    send.assert_awaited_once_with(
        job.url,
        payload["content"],
        10000.0,
        20000.0,
        offer_price=8500,
        duration_days=7,
        submission_action=None,
        offer_title="Test title",
        payment_type_label="Безопасная",
    )


@pytest.mark.asyncio
async def test_invalid_payload_never_opens_browser(monkeypatch):
    from app.services import kwork_browser

    browser_send = AsyncMock()
    monkeypatch.setattr(kwork_browser, "_send_kwork_offer_locked", browser_send)
    ok, _, debug = await kwork_browser.send_kwork_offer("https://example.test", "x" * 150)
    assert not ok and debug["error_label"] == "invalid_approved_payload"
    browser_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_edit_entrypoint_is_closed():
    from app.services.kwork_browser import _edit_existing_offer

    ok, _, debug = await _edit_existing_offer(
        None, "", "x" * 150, None, None, [], "1", content_file=None, offer_price=500
    )
    assert not ok and debug["error_label"] == "edit_requires_approval"
