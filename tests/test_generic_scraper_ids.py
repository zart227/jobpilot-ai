from app.scrapers.generic import GenericMarketplaceScraper


def test_generic_external_id_stable_for_same_url() -> None:
    scraper = GenericMarketplaceScraper(url="https://example.com/jobs", selectors={})
    url = "https://example.com/jobs/42/some-title"
    first = scraper._external_id_from_url(url)
    second = scraper._external_id_from_url(url)
    assert first == second
    assert first is not None


def test_generic_external_id_differs_for_different_paths() -> None:
    scraper = GenericMarketplaceScraper(url="https://example.com/jobs", selectors={})
    a = scraper._external_id_from_url("https://example.com/jobs/1")
    b = scraper._external_id_from_url("https://example.com/jobs/2")
    assert a != b
