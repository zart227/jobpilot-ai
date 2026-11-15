from pathlib import Path

import pytest

from app.scrapers.weblancer import (
    WeblancerScraper,
    weblancer_project_id_from_url,
)


def test_weblancer_project_id_from_url() -> None:
    url = "https://www.weblancer.net/freelance/veb-programmirovanie-31/parsing-saita-1203726/"
    assert weblancer_project_id_from_url(url) == "1203726"


@pytest.mark.asyncio
async def test_weblancer_extract_modern_row_from_fixture() -> None:
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    html = Path(__file__).parent.joinpath("fixtures/weblancer_listing_snippet.html").read_text()
    scraper = WeblancerScraper.__new__(WeblancerScraper)
    scraper._simple_only = False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html, wait_until="domcontentloaded")
        jobs = await scraper._extract_listing_page(page)
        await browser.close()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.platform == "weblancer"
    assert job.external_id == "weblancer-1203726"
    assert job.title == "Парсинг сайта"
    assert job.budget_min == 5000
