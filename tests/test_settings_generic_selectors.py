"""Settings validation for generic scraper JSON config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_generic_scraper_selectors_accepts_empty_object() -> None:
    settings = Settings(GENERIC_SCRAPER_SELECTORS="{}")
    assert settings.generic_scraper_selectors == "{}"


def test_generic_scraper_selectors_rejects_invalid_json() -> None:
    with pytest.raises(ValidationError, match="GENERIC_SCRAPER_SELECTORS"):
        Settings(GENERIC_SCRAPER_SELECTORS="{job_card:article}")


def test_generic_scraper_selectors_rejects_non_object() -> None:
    with pytest.raises(ValidationError, match="JSON object"):
        Settings(GENERIC_SCRAPER_SELECTORS='["a"]')


def test_cached_settings_respects_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("GENERIC_SCRAPER_SELECTORS", '{"title":"h1"}')
    settings = get_settings()
    assert settings.generic_scraper_selectors == '{"title":"h1"}'
    get_settings.cache_clear()
