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
    llm_simple_provider: Literal["ollama", "openai", "anthropic", "cursor", "same"] = Field(
        default="ollama",
        alias="LLM_SIMPLE_PROVIDER",
        description="Provider for filter/score/chat/learning; proposal uses LLM_PROVIDER",
    )
    ollama_base_url: str = Field(
        default="https://ollama.com",
        alias="OLLAMA_BASE_URL",
    )
    ollama_api_key: str = Field(
        default="",
        alias="OLLAMA_API_KEY",
        description="Ollama Cloud API key (ollama.com/settings/keys); optional for local ollama signin",
    )
    ollama_model: str = Field(default="minimax-m2.5", alias="OLLAMA_MODEL")
    ollama_timeout_seconds: float = Field(default=120.0, alias="OLLAMA_TIMEOUT_SECONDS")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_timeout_seconds: float = Field(default=30.0, alias="OPENAI_TIMEOUT_SECONDS")
    openai_max_retries: int = Field(default=3, alias="OPENAI_MAX_RETRIES")
    proxy: str = Field(default="", alias="PROXY")
    openai_proxy_list: str = Field(
        default="",
        alias="OPENAI_PROXY_LIST",
        description="Path to proxy list for OpenAI (e.g. Poland residential)",
    )
    telegram_proxy_list: str = Field(
        default="",
        alias="TELEGRAM_PROXY_LIST",
        description="Path to proxy list for Telegram bot",
    )
    proxy_rotate: bool = Field(default=True, alias="PROXY_ROTATE")
    proxy_rotate_mode: str = Field(default="sequential", alias="PROXY_ROTATE_MODE")
    proxy_max_attempts: int = Field(default=5, alias="PROXY_MAX_ATTEMPTS")
    proxy_state_dir: str = Field(default="./data", alias="PROXY_STATE_DIR")
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
