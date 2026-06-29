"""Upwork scraper stub — extend with platform-specific login and selectors."""

import structlog

from app.scrapers.generic import GenericMarketplaceScraper

logger = structlog.get_logger(__name__)


class UpworkScraper(GenericMarketplaceScraper):
    platform = "upwork"

    def __init__(self) -> None:
        super().__init__(
            url="https://www.upwork.com/nx/search/jobs/",
            selectors={
                "job_card": "article.job-tile, [data-test='job-tile']",
                "title": "h2, [data-test='job-title']",
                "description": "[data-test='job-description-text'], .description",
                "budget": "[data-test='budget'], .budget",
                "skills": "[data-test='skill'], .skill",
                "deadline": "time",
                "client": "[data-test='client']",
                "link": "a",
            },
        )
        logger.info("JobPilot AI Upwork scraper initialized")
