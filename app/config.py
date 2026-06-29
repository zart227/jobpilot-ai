from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="JobPilot AI", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=False, alias="DEBUG")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    database_url: str = Field(
        default="postgresql+asyncpg://jobpilot:jobpilot_secret@localhost:5432/jobpilot_ai",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    celery_broker_url: str = Field(default="redis://localhost:6379/0", alias="CELERY_BROKER_URL")
    celery_result_backend: str = Field(
        default="redis://localhost:6379/1", alias="CELERY_RESULT_BACKEND"
    )

    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_collection_prefix: str = Field(
        default="jobpilot_ai", alias="QDRANT_COLLECTION_PREFIX"
    )

    llm_provider: Literal["openai", "anthropic", "cursor"] = Field(
        default="openai", alias="LLM_PROVIDER"
    )
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-3-5-haiku-latest", alias="ANTHROPIC_MODEL")
    cursor_api_key: str = Field(default="", alias="CURSOR_API_KEY")
    cursor_model: str = Field(default="composer-2.5", alias="CURSOR_MODEL")
    cursor_workspace: str = Field(default=".", alias="CURSOR_WORKSPACE")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_admin_chat_id: str = Field(default="", alias="TELEGRAM_ADMIN_CHAT_ID")

    developer_name: str = Field(default="Developer", alias="DEVELOPER_NAME")
    developer_skills: str = Field(
        default="Python,FastAPI,PostgreSQL,Docker", alias="DEVELOPER_SKILLS"
    )
    developer_hourly_rate: float = Field(default=50.0, alias="DEVELOPER_HOURLY_RATE")
    developer_bio: str = Field(
        default="Experienced freelance developer.", alias="DEVELOPER_BIO"
    )
    developer_portfolio: str = Field(
        default="",
        alias="DEVELOPER_PORTFOLIO",
        description="Relevant projects for Kwork offers: name, stack, link per line",
    )
    developer_excluded_skills: str = Field(
        default="n8n", alias="DEVELOPER_EXCLUDED_SKILLS"
    )

    scrape_interval_minutes: int = Field(default=30, alias="SCRAPE_INTERVAL_MINUTES")
    enabled_scrapers: str = Field(default="generic,kwork", alias="ENABLED_SCRAPERS")
    generic_scraper_url: str = Field(
        default="https://example-freelance.com/jobs", alias="GENERIC_SCRAPER_URL"
    )
    generic_scraper_selectors: str = Field(default="{}", alias="GENERIC_SCRAPER_SELECTORS")

    kwork_email: str = Field(default="", alias="KWORK_EMAIL")
    kwork_password: str = Field(default="", alias="KWORK_PASSWORD")
    kwork_category_url: str = Field(
        default="https://kwork.ru/projects?a=1", alias="KWORK_CATEGORY_URL"
    )
    kwork_max_pages: int = Field(default=5, alias="KWORK_MAX_PAGES")
    kwork_scrape_login: bool = Field(
        default=False,
        alias="KWORK_SCRAPE_LOGIN",
        description="Login before scraping (skewed listing); keep false for public category feed",
    )
    kwork_storage_state: str = Field(default="", alias="KWORK_STORAGE_STATE")
    kwork_offer_discount_percent: float = Field(
        default=15.0,
        alias="KWORK_OFFER_DISCOUNT_PERCENT",
        description="Bid this % below buyer desired budget (competitive pricing)",
    )

    min_score_threshold: int = Field(default=35, alias="MIN_SCORE_THRESHOLD")

    scoring_weights: str = Field(
        default='{"skill_match":0.30,"budget":0.20,"complexity":0.15,"client_quality":0.20,"competition":0.15}',
        alias="SCORING_WEIGHTS",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
