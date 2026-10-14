"""Shared pytest hooks."""

import os

import pytest

from app.config import (
    _drop_invalid_generic_scraper_selectors_env,
    _drop_legacy_shell_database_url,
    get_settings,
)

_drop_legacy_shell_database_url()
_drop_invalid_generic_scraper_selectors_env()
get_settings.cache_clear()


def pytest_configure() -> None:
    """Integration tests must never use the application's working database/broker."""
    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        return
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    database_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    try:
        database_name = make_url(database_url).database or ""
        redis_database = int(make_url(redis_url).database or "0")
    except (ValueError, ArgumentError) as exc:
        raise pytest.UsageError("Integration tests require explicit test DATABASE_URL/REDIS_URL") from exc
    if not database_name.endswith("_test") or redis_database < 2:
        raise pytest.UsageError(
            "Integration tests require a database ending in _test and REDIS_URL database >= 2; "
            "the working database and broker are unsafe for tests."
        )
    settings = get_settings()
    for url in (settings.celery_broker_url, settings.celery_result_backend):
        if int(make_url(url).database or "0") < 2:
            raise pytest.UsageError(
                "Integration tests require CELERY_BROKER_URL and CELERY_RESULT_BACKEND "
                "to use Redis databases >= 2."
            )


@pytest.fixture(autouse=True)
def disable_api_rate_limit_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """TestClient hits main app; avoid Redis rate limit coupling in unit tests."""
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def dispose_sqlalchemy_engine_after_test() -> None:
    """Avoid asyncpg 'attached to a different loop' when integration tests reuse the pool."""
    yield
    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        return
    from app.db.session import engine

    await engine.dispose()
