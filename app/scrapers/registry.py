import structlog

from app.config import get_settings
from app.scrapers.base import BaseScraper
from app.scrapers.fl_ru import FlRuScraper
from app.scrapers.generic import GenericMarketplaceScraper
from app.scrapers.kwork import KworkScraper
from app.scrapers.upwork import UpworkScraper

logger = structlog.get_logger(__name__)

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "generic": GenericMarketplaceScraper,
    "kwork": KworkScraper,
    "upwork": UpworkScraper,
    "fl_ru": FlRuScraper,
}


def get_scrapers() -> list[BaseScraper]:
    settings = get_settings()
    enabled = [s.strip() for s in settings.enabled_scrapers.split(",") if s.strip()]
    scrapers: list[BaseScraper] = []

    for name in enabled:
        cls = SCRAPER_REGISTRY.get(name)
        if cls:
            scrapers.append(cls())
            logger.info("JobPilot AI scraper registered", name=name)
        else:
            logger.warning("Unknown scraper skipped", name=name)

    return scrapers
