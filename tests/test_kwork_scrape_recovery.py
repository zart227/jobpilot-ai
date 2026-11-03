"""One failed route or page must not discard recoverable Kwork listings."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.scrapers.kwork import KworkScraper


async def test_browser_launch_failure_rotates_to_next_route(monkeypatch):
    from app.services import kwork_browser, kwork_pause
    from app.utils import proxy

    scraper = KworkScraper.__new__(KworkScraper)
    scraper._email = scraper._password = ''
    scraper._scrape_listing_pages = AsyncMock(return_value=[SimpleNamespace(external_id='kwork-123')])
    settings = SimpleNamespace(kwork_scrape_login=False)
    monkeypatch.setattr('app.scrapers.kwork.get_settings', lambda: settings)
    monkeypatch.setattr(kwork_pause, 'is_kwork_paused', lambda: False)
    monkeypatch.setattr(proxy, 'get_proxy_candidates', lambda *_: ['http://a:1', 'http://b:2'])
    launch = AsyncMock(side_effect=[RuntimeError('failed'), ('playwright', 'browser', 'page')])
    monkeypatch.setattr(kwork_browser, 'launch_browser', launch)
    close = AsyncMock()
    monkeypatch.setattr(kwork_browser, 'close_browser', close)
    result = await scraper.scrape()
    assert len(result) == 1 and launch.await_count == 2
    close.assert_awaited_once_with('playwright', 'browser')


async def test_later_failed_page_retains_already_parsed_jobs():
    scraper = KworkScraper.__new__(KworkScraper)
    scraper._max_pages = 2
    scraper._category_url = 'https://example.invalid/projects'
    scraper._extract_kwork_card = AsyncMock(return_value=SimpleNamespace(external_id='kwork-123'))
    cards = SimpleNamespace(count=AsyncMock(return_value=1), nth=lambda _: 'card')
    page = SimpleNamespace(goto=AsyncMock(side_effect=[None, RuntimeError('offline')]),
                           wait_for_selector=AsyncMock(), wait_for_timeout=AsyncMock(),
                           locator=lambda _: cards)
    result = await scraper._scrape_listing_pages(page)
    assert len(result) == 1 and result[0].external_id == 'kwork-123'
