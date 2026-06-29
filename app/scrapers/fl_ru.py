"""FL.ru scraper stub — extend with platform-specific login and selectors."""

import structlog

from app.scrapers.generic import GenericMarketplaceScraper

logger = structlog.get_logger(__name__)


class FlRuScraper(GenericMarketplaceScraper):
    platform = "fl_ru"

    def __init__(self) -> None:
        super().__init__(
            url="https://www.fl.ru/projects/",
            selectors={
                "job_card": ".b-post, .project-item",
                "title": "h2 a, .b-post__title",
                "description": ".b-post__txt, .description",
                "budget": ".b-post__price, .budget",
                "skills": ".b-post__tags a, .tag",
                "deadline": ".deadline",
                "client": ".b-post__author",
                "link": "a",
            },
        )
        logger.info("JobPilot AI FL.ru scraper initialized")
