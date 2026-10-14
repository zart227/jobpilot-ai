import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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
    platform_dry_run: bool = Field(
        default=False,
        alias="PLATFORM_DRY_RUN",
        description="Skip real Kwork/Telegram platform API calls; still records send_attempts and DB state",
    )
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_auth_token: str = Field(
        default="",
        alias="API_AUTH_TOKEN",
        description="Bearer or X-API-Key token for HTTP API (required when API_AUTH_REQUIRED=true)",
    )
    api_auth_required: bool = Field(default=False, alias="API_AUTH_REQUIRED")
    api_list_limit_max: int = Field(default=200, ge=1, le=1000, alias="API_LIST_LIMIT_MAX")
    api_rate_limit_enabled: bool = Field(default=True, alias="API_RATE_LIMIT_ENABLED")
    api_rate_limit_requests: int = Field(
        default=120,
        ge=1,
        le=10_000,
        alias="API_RATE_LIMIT_REQUESTS",
        description="Max authenticated/anonymous API requests per client per window",
    )
    api_rate_limit_window_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        alias="API_RATE_LIMIT_WINDOW_SECONDS",
    )
    api_rate_limit_fail_open: bool = Field(
        default=True,
        alias="API_RATE_LIMIT_FAIL_OPEN",
        description="If Redis is down, allow traffic (false = 503)",
    )

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

    llm_provider: Literal["openai", "anthropic", "cursor", "openrouter"] = Field(
        default="openai", alias="LLM_PROVIDER"
    )
    llm_simple_provider: Literal["ollama", "openai", "anthropic", "cursor", "openrouter", "same"] = Field(
        default="ollama",
        alias="LLM_SIMPLE_PROVIDER",
        description="Provider for filter/learning/summaries; scoring uses OLLAMA_SCORING_MODEL; proposals and chats use LLM_PROVIDER",
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
    ollama_model: str = Field(default="nemotron-3-nano:30b", alias="OLLAMA_MODEL")
    ollama_scoring_model: str = Field(default="gpt-oss:120b", alias="OLLAMA_SCORING_MODEL")
    ollama_timeout_seconds: float = Field(default=120.0, alias="OLLAMA_TIMEOUT_SECONDS")
    ollama_session_cookie: str = Field(
        default="",
        alias="OLLAMA_SESSION_COOKIE",
        description="Browser __Secure-session cookie for ollama.com/settings usage scrape",
    )
    ollama_aid: str = Field(
        default="",
        alias="OLLAMA_AID",
        description="Browser aid cookie for ollama.com/settings usage scrape",
    )
    ollama_cf_clearance: str = Field(
        default="",
        alias="OLLAMA_CF_CLEARANCE",
        description="Optional Cloudflare cf_clearance cookie for settings scrape",
    )
    ollama_usage_warn_percent: float = Field(default=85.0, alias="OLLAMA_USAGE_WARN_PERCENT")
    ollama_usage_critical_percent: float = Field(default=95.0, alias="OLLAMA_USAGE_CRITICAL_PERCENT")
    ollama_usage_cache_seconds: int = Field(default=60, alias="OLLAMA_USAGE_CACHE_SECONDS")
    ollama_usage_alert_cooldown_seconds: int = Field(
        default=3600,
        alias="OLLAMA_USAGE_ALERT_COOLDOWN_SECONDS",
    )
    ollama_auto_fallback: bool = Field(
        default=True,
        alias="OLLAMA_AUTO_FALLBACK",
        description="Fall back to OPENAI_SIMPLE_MODEL for simple LLM when Ollama limits are critical",
    )
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="openrouter/free", alias="OPENROUTER_MODEL")
    openrouter_simple_model: str = Field(default="openrouter/free", alias="OPENROUTER_SIMPLE_MODEL")
    openrouter_timeout_seconds: float = Field(default=120.0, gt=0, alias="OPENROUTER_TIMEOUT_SECONDS")
    openrouter_fallback_to_ollama: bool = Field(default=True, alias="OPENROUTER_FALLBACK_TO_OLLAMA")
    openrouter_proxy_list: str = Field(default="", alias="OPENROUTER_PROXY_LIST")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_simple_model: str = Field(
        default="gpt-4.1-nano",
        alias="OPENAI_SIMPLE_MODEL",
        description="Cheap OpenAI model for filter/score/chat and Ollama auto-fallback",
    )
    openai_timeout_seconds: float = Field(default=30.0, alias="OPENAI_TIMEOUT_SECONDS")
    openai_max_retries: int = Field(default=3, alias="OPENAI_MAX_RETRIES")
    openai_admin_api_key: str = Field(
        default="",
        alias="OPENAI_ADMIN_API_KEY",
        description="Admin API key for organization/costs spend tracking",
    )
    openai_session_token: str = Field(
        default="",
        alias="OPENAI_SESSION_TOKEN",
        description="Browser session Bearer token for billing/credit_grants (platform.openai.com)",
    )
    openai_session_cookie: str = Field(
        default="",
        alias="OPENAI_SESSION_COOKIE",
        description="Optional browser Cookie header (used together with OPENAI_SESSION_TOKEN)",
    )
    openai_budget_usd: float = Field(
        default=0.0,
        alias="OPENAI_BUDGET_USD",
        description="Manual prepaid balance for estimated remaining when credits API unavailable",
    )
    openai_balance_warn_usd: float = Field(default=5.0, alias="OPENAI_BALANCE_WARN_USD")
    openai_balance_critical_usd: float = Field(default=2.0, alias="OPENAI_BALANCE_CRITICAL_USD")
    openai_usage_cache_seconds: int = Field(default=300, alias="OPENAI_USAGE_CACHE_SECONDS")
    openai_usage_alert_cooldown_seconds: int = Field(
        default=3600,
        alias="OPENAI_USAGE_ALERT_COOLDOWN_SECONDS",
    )
    openai_cost_lookback_days: int = Field(default=30, alias="OPENAI_COST_LOOKBACK_DAYS")
    proxy: str = Field(
        default="",
        alias="PROXY",
        description="Single proxy for all services (HTTP/SOCKS5), e.g. v2raytun local port",
    )
    proxy_enabled: bool = Field(
        default=True,
        alias="PROXY_ENABLED",
        description="Master switch: false = direct connection, no proxy lists",
    )
    openai_proxy_list: str = Field(
        default="",
        alias="OPENAI_PROXY_LIST",
        description="Path to proxy list for OpenAI (e.g. Poland residential)",
    )
    ollama_proxy_list: str = Field(
        default="",
        alias="OLLAMA_PROXY_LIST",
        description="Path to proxy list for Ollama Cloud; falls back to OPENAI_PROXY_LIST",
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
    cursor_workspace_host: str = Field(
        default="",
        alias="CURSOR_WORKSPACE_HOST",
        description="Host path to project for Cursor agent (required in Docker)",
    )
    cursor_bridge_port: int = Field(default=9247, alias="CURSOR_BRIDGE_PORT")
    jobpilot_in_docker: bool = Field(default=False, alias="JOBPILOT_IN_DOCKER")
    cursor_agent_timeout_seconds: float = Field(
        default=600.0,
        alias="CURSOR_AGENT_TIMEOUT_SECONDS",
        description="Timeout for dev-agent runs via Cursor SDK bridge",
    )
    cursor_bridge_base_url: str = Field(
        default="",
        alias="CURSOR_BRIDGE_BASE_URL",
        description="External bridge URL when bot runs in Docker (from run_cursor_bridge.py)",
    )
    cursor_bridge_auth_token: str = Field(
        default="",
        alias="CURSOR_BRIDGE_AUTH_TOKEN",
        description="Auth token printed by run_cursor_bridge.py",
    )
    telegram_dev_agent_enabled: bool = Field(
        default=True,
        alias="TELEGRAM_DEV_AGENT_ENABLED",
        description="Allow /dev commands to talk to local Cursor agent",
    )
    dev_agent_log_enabled: bool = Field(
        default=True,
        alias="DEV_AGENT_LOG_ENABLED",
        description="Write dev-agent request/response audit logs to data/dev_agent_logs",
    )
    dev_agent_log_dir: str = Field(
        default="data/dev_agent_logs",
        alias="DEV_AGENT_LOG_DIR",
    )

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_admin_chat_id: str = Field(default="", alias="TELEGRAM_ADMIN_CHAT_ID")
    telegram_admin_user_id: str = Field(
        default="",
        alias="TELEGRAM_ADMIN_USER_ID",
        description="Telegram user id allowed to approve/send (recommended in production)",
    )

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

    review_first_mode: bool = Field(default=True, alias="REVIEW_FIRST_MODE")
    review_max_hours: float = Field(default=8.0, gt=0, alias="REVIEW_MAX_HOURS")
    client_min_rating: float = Field(default=4.8, ge=0, le=5, alias="CLIENT_MIN_RATING")
    client_min_paid_orders: int = Field(default=1, ge=1, alias="CLIENT_MIN_PAID_ORDERS")

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
    kwork_skip_portfolio_jobs: bool = Field(
        default=True,
        alias="KWORK_SKIP_PORTFOLIO_JOBS",
        description="Skip Kwork jobs that require portfolio kworks (sites/SaaS/apps)",
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
    kwork_pause_enabled: bool = Field(
        default=True,
        alias="KWORK_PAUSE_ENABLED",
        description="Pause Kwork scrape, LLM pipeline and proposal delivery when connects exhausted",
    )
    kwork_pause_auto: bool = Field(
        default=True,
        alias="KWORK_PAUSE_AUTO",
        description="Auto-detect connects limit and resume date from Kwork UI",
    )
    kwork_pause_until: str = Field(
        default="",
        alias="KWORK_PAUSE_UNTIL",
        description="Manual resume date override (YYYY-MM-DD); used when KWORK_PAUSE_AUTO=false",
    )
    kwork_pause_timezone: str = Field(
        default="Europe/Moscow",
        alias="KWORK_PAUSE_TIMEZONE",
    )
    kwork_pause_check_interval_hours: int = Field(
        default=6,
        alias="KWORK_PAUSE_CHECK_INTERVAL_HOURS",
    )
    kwork_pause_state_file: str = Field(
        default="data/kwork_connects_pause.json",
        alias="KWORK_PAUSE_STATE_FILE",
    )
    kwork_inbox_check_enabled: bool = Field(
        default=True,
        alias="KWORK_INBOX_CHECK_ENABLED",
        description="Poll Kwork inbox for new client messages and notify via Telegram",
    )
    kwork_inbox_check_interval_minutes: int = Field(
        default=15,
        alias="KWORK_INBOX_CHECK_INTERVAL_MINUTES",
    )
    job_processing_lease_seconds: int = Field(
        default=900,
        ge=60,
        alias="JOB_PROCESSING_LEASE_SECONDS",
    )
    job_processing_backoff_seconds: int = Field(
        default=120,
        ge=10,
        alias="JOB_PROCESSING_BACKOFF_SECONDS",
    )
    kwork_pause_block_inbox_on_connects: bool = Field(
        default=False,
        alias="KWORK_PAUSE_BLOCK_INBOX_ON_CONNECTS",
        description="When true, connects-exhausted pause also stops inbox polling",
    )
    send_attempt_max_attempts: int = Field(
        default=5,
        ge=1,
        alias="SEND_ATTEMPT_MAX_ATTEMPTS",
    )
    send_attempt_lease_seconds: int = Field(
        default=180,
        ge=30,
        alias="SEND_ATTEMPT_LEASE_SECONDS",
    )
    send_attempt_base_backoff_seconds: int = Field(
        default=60,
        ge=1,
        alias="SEND_ATTEMPT_BASE_BACKOFF_SECONDS",
    )
    notification_outbox_max_attempts: int = Field(
        default=8,
        ge=1,
        alias="NOTIFICATION_OUTBOX_MAX_ATTEMPTS",
    )
    notification_outbox_lease_seconds: int = Field(
        default=120,
        ge=10,
        alias="NOTIFICATION_OUTBOX_LEASE_SECONDS",
    )
    notification_outbox_base_backoff_seconds: int = Field(
        default=30,
        ge=1,
        alias="NOTIFICATION_OUTBOX_BASE_BACKOFF_SECONDS",
    )
    kwork_inbox_state_file: str = Field(
        default="data/kwork_inbox_state.json",
        alias="KWORK_INBOX_STATE_FILE",
    )

    min_score_threshold: int = Field(default=35, alias="MIN_SCORE_THRESHOLD")
    job_max_age_hours: int = Field(
        default=168,
        ge=1,
        alias="JOB_MAX_AGE_HOURS",
        description="Skip proposal generation for jobs older than this many hours",
    )
    job_skip_expired_deadlines: bool = Field(
        default=True,
        alias="JOB_SKIP_EXPIRED_DEADLINES",
    )

    scoring_weights: str = Field(
        default='{"skill_match":0.30,"budget":0.20,"complexity":0.15,"client_quality":0.20,"competition":0.15}',
        alias="SCORING_WEIGHTS",
    )
    learning_min_outcomes: int = Field(
        default=10,
        ge=1,
        alias="LEARNING_MIN_OUTCOMES",
        description="Minimum recorded outcomes before activating learned scoring weights",
    )
    embedding_provider: str = Field(
        default="openai",
        alias="EMBEDDING_PROVIDER",
        description="Embedding API for Qdrant (independent from LLM_PROVIDER)",
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="EMBEDDING_MODEL",
    )
    embedding_dimensions: int = Field(
        default=1536,
        ge=1,
        alias="EMBEDDING_DIMENSIONS",
    )

    @field_validator("generic_scraper_selectors")
    @classmethod
    def _validate_generic_scraper_selectors(cls, value: str) -> str:
        try:
            parsed = json.loads(value or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(
                "GENERIC_SCRAPER_SELECTORS must be valid JSON (object of CSS selector strings)"
            ) from exc
        if not isinstance(parsed, dict):
            # Pydantic wraps ValueError in ValidationError; TypeError escapes validation.
            raise ValueError("GENERIC_SCRAPER_SELECTORS must be a JSON object")  # noqa: TRY004
        return value


def _drop_legacy_shell_database_url() -> None:
    """README/compose use host 5434; stale shell exports often still point at 5433."""
    import os

    url = os.environ.get("DATABASE_URL", "")
    if ":5433/" in url:
        os.environ.pop("DATABASE_URL", None)


def _drop_invalid_generic_scraper_selectors_env() -> None:
    """Prefer .env when the process environment has a dotenv-mangled selectors string."""
    import os

    raw = os.environ.get("GENERIC_SCRAPER_SELECTORS")
    if not raw:
        return
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return
    except json.JSONDecodeError:
        pass
    os.environ.pop("GENERIC_SCRAPER_SELECTORS", None)


@lru_cache
def get_settings() -> Settings:
    _drop_legacy_shell_database_url()
    _drop_invalid_generic_scraper_selectors_env()
    return Settings()
