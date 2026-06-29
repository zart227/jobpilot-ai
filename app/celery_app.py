from datetime import timedelta

import structlog
from celery import Celery

from app.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

celery_app = Celery(
    "jobpilot_ai",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "scrape-jobs-periodically": {
            "task": "app.tasks.scrape_tasks.run_all_scrapers",
            "schedule": timedelta(minutes=max(1, settings.scrape_interval_minutes)),
        },
    },
)

import app.tasks.scrape_tasks  # noqa: E402, F401
