from pathlib import Path

import pytest

from app.config import Settings
from app.scrapers.fl_ru import (
    FlRuScraper,
    description_needs_detail_fetch,
    listing_page_url,
)


def test_fl_ru_page_url_appends_page_query() -> None:
    base = "https://www.fl.ru/projects/?kind=1"
    assert listing_page_url(base, 1) == base
    assert listing_page_url(base, 2) == "https://www.fl.ru/projects/?kind=1&page=2"


def test_fl_ru_description_needs_detail_fetch() -> None:
    assert description_needs_detail_fetch("Коротко...")
    assert description_needs_detail_fetch("")
    assert not description_needs_detail_fetch("x" * 200)


def test_fl_ru_external_id_from_project_url() -> None:
    scraper = FlRuScraper.__new__(FlRuScraper)
    eid = scraper._project_external_id(
        "https://www.fl.ru/projects/5528402/foo.html",
        "Title",
        "",
    )
    assert eid == "fl_ru-5528402"


@pytest.mark.asyncio
async def test_fl_ru_extract_card_from_fixture() -> None:
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    html = Path(__file__).parent.joinpath("fixtures/fl_ru_listing_snippet.html").read_text()
    scraper = FlRuScraper.__new__(FlRuScraper)
    scraper._simple_only = False
    scraper._free_offers_only = False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="domcontentloaded")
        card = page.locator("div.b-post[qa-project-name]").first
        job = await scraper._extract_card(card, page, 0)
        await browser.close()

    assert job is not None
    assert job.platform == "fl_ru"
    assert job.external_id == "fl_ru-5528402"
    assert job.title == "Senior monday.com"
    assert job.budget_min == 5000
    assert job.budget_max == 10000
    assert job.budget_currency == "RUB"
    assert job.url.endswith("/projects/5528402/senior-arhitektor-mondaycom.html")


def test_fl_ru_settings_fields_present() -> None:
    s = Settings()
    assert "fl.ru" in s.fl_ru_listing_url
    assert s.fl_ru_max_pages >= 1
    assert hasattr(s, "fl_ru_email")
    assert hasattr(s, "fl_ru_password")
    assert hasattr(s, "fl_ru_storage_state")
