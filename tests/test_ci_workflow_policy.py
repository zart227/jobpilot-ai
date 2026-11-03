"""CI workflow must run lint/tests without GitHub Actions secrets or production credentials."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_workflow_does_not_reference_github_secrets() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    assert "secrets." not in text, "CI must not inject repository/org secrets into jobs"


def test_ci_lockfile_job_runs_ruff_and_pytest() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    assert "lockfile-lint-test:" in text
    assert "ruff check app tests scripts" in text
    assert "pytest -q" in text
    assert "pip check" in text


def test_ci_browser_job_uses_empty_external_credentials() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    browser = text.split("browser-form-contract:", 1)[-1].split("docker-build:", 1)[0]
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "KWORK_EMAIL",
        "KWORK_PASSWORD",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CURSOR_API_KEY",
    ):
        assert f'{key}: ""' in browser, f"expected empty {key} in browser CI job"


def test_live_signoff_json_is_gitignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/live_kwork_signoff.json" in ignore
