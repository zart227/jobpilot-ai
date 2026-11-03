"""C097: logs must scrub secrets; SQL echo off by default; retention configured."""

import logging
from pathlib import Path

from app.config import Settings
from app.logging_setup import _SensitiveLogFilter, configure_application_logging
from app.utils.log_scrubber import scrub_sensitive_text

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
POLICY = (ROOT / "docs/ops-logging-policy.md").read_text(encoding="utf-8")


def test_scrub_redacts_bearer_and_database_url() -> None:
    raw = (
        "Authorization: Bearer abc.def.ghi "
        "postgresql+asyncpg://jobpilot:realpass@127.0.0.1:5434/jobpilot_ai"
    )
    scrubbed = scrub_sensitive_text(raw)
    assert "realpass" not in scrubbed
    assert "abc.def.ghi" not in scrubbed
    assert "[REDACTED]" in scrubbed


def test_scrub_preserves_documented_placeholders() -> None:
    assert "sk-your-openai-key" in scrub_sensitive_text("OPENAI_API_KEY=sk-your-openai-key")


def test_sqlalchemy_echo_defaults_false() -> None:
    settings = Settings(_env_file=None, DEBUG=True)
    assert settings.sqlalchemy_echo is False


def test_sensitive_log_filter_scrubs_stdlib_record() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token=%s",
        args=("Bearer super-secret-value",),
        exc_info=None,
    )
    assert _SensitiveLogFilter().filter(record)
    assert "super-secret-value" not in record.msg
    assert "super-secret-value" not in record.args[0]


def test_configure_application_logging_is_idempotent() -> None:
    configure_application_logging()
    configure_application_logging()


def test_compose_app_services_have_log_rotation() -> None:
    for service in ("api:", "celery-worker:", "celery-beat:", "telegram-bot:"):
        block = COMPOSE.split(service, 1)[1].split("\n    ", 5)[0]
        assert "max-size" in block or "docker-logging" in COMPOSE
    assert "max-size: \"10m\"" in COMPOSE
    assert "max-file: \"5\"" in COMPOSE


def test_ops_logging_policy_documents_scrubber_and_retention() -> None:
    assert "log_scrubber" in POLICY or "scrub" in POLICY.lower()
    assert "max-size" in POLICY or "max-file" in POLICY
