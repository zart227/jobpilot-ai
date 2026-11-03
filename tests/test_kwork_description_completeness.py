"""Listing descriptions must not be assessed when expansion is unconfirmed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.scrapers.kwork import KworkScraper


class Locator:
    def __init__(self, text='', *, count=1, visible=False, click_error=None):
        self.first = self
        self._text = text
        self._count = count
        self._visible = visible
        self._click_error = click_error

    async def count(self):
        return self._count

    async def click(self):
        if self._click_error:
            raise self._click_error

    async def is_visible(self):
        return self._visible

    def nth(self, index):
        return self

    async def text_content(self):
        return self._text


@pytest.mark.asyncio
@pytest.mark.parametrize(('expand', 'description', 'detail', 'expected'), [
    (Locator(count=1, click_error=RuntimeError('failed')), 'Partial ' * 30, '', ''),
    (Locator(count=1, visible=True), 'Partial ' * 30, '', ''),
    (Locator(count=1, visible=True), 'Partial ' * 30, 'Full detail text', 'Full detail text'),
    (Locator(count=0), 'Partial ' * 30 + '...', '', ''),
    (Locator(count=1, visible=False), 'Full ' * 40, '', ('Full ' * 40).strip()),
])
async def test_incomplete_description_requires_full_detail(expand, description, detail, expected):
    scraper = KworkScraper.__new__(KworkScraper)
    scraper._fetch_detail_description = AsyncMock(return_value=detail)
    card = SimpleNamespace(locator=lambda selector: (
        expand if 'Показать полностью' in selector else Locator(description)
    ))
    page = SimpleNamespace(wait_for_timeout=AsyncMock())
    assert await scraper._read_description(card, page, 'https://example.test/project') == expected
