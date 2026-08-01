from app.llm.errors import LLMErrorKind, classify_llm_error
from app.llm.ollama_usage import parse_settings_html

SAMPLE_HTML = """
<h2>Cloud usage</h2>
<div class="flex justify-between mb-2">
  <span>Session usage</span>
  <span>82.4% used</span>
</div>
<div class="local-time" data-time="2026-07-08T01:00:00Z">Resets in 4 hours</div>
<div class="flex justify-between mb-2">
  <span>Weekly usage</span>
  <span>64.3% used</span>
</div>
<div class="local-time">Resets in 5 days</div>
"""


def test_parse_settings_html_extracts_session_and_weekly() -> None:
    session, weekly, error = parse_settings_html(SAMPLE_HTML)
    assert error is None
    assert session is not None
    assert weekly is not None
    assert session.percent == 82.4
    assert weekly.percent == 64.3
    assert session.reset_text == "4 hours"
    assert weekly.reset_text == "5 days"


def test_parse_settings_html_detects_cloudflare() -> None:
    session, weekly, error = parse_settings_html("<html>Just a moment...</html>")
    assert session is None
    assert weekly is None
    assert error is not None
    assert "Cloudflare" in error


class _FakeOllamaError(Exception):
    status_code = 429
    error = "you have reached your session usage limit, upgrade for higher limits"


def test_classify_session_quota_error() -> None:
    err = classify_llm_error(_FakeOllamaError())
    assert err.kind == LLMErrorKind.QUOTA
    assert err.quota_period == "session"
    assert not err.retryable
