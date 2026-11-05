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


def test_ci_lockfile_job_runs_remediation_verify() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    lockfile = text.split("lockfile-lint-test:", 1)[1].split("integration:", 1)[0]
    assert "./scripts/remediation_verify.sh" in lockfile


def test_ci_lockfile_job_runs_secret_scan_and_audit() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    lockfile = text.split("lockfile-lint-test:", 1)[1].split("integration:", 1)[0]
    assert "secret_scan_repo.py" in lockfile
    assert "audit_remediation_status.py" in lockfile


def test_ci_lockfile_job_expects_live_signoff_absent() -> None:
    text = CI_YML.read_text(encoding="utf-8")
    lockfile = text.split("lockfile-lint-test:", 1)[1].split("integration:", 1)[0]
    assert "validate_live_signoff.py" in lockfile
    assert "live_kwork_signoff.json must not be committed" in lockfile


def test_remediation_verify_uses_nonblocking_checklist_threshold() -> None:
    verify = (ROOT / "scripts" / "remediation_verify.sh").read_text(encoding="utf-8")
    assert "REMEDIATION_MAX_OPEN=115" in verify
    assert "test_ci_workflow_policy.py" in verify
    assert "test_c090_c093_production_repro_policy.py" in verify or "[ -f" in verify


def test_remediation_verify_includes_c090_gate_only_when_file_exists() -> None:
    verify = (ROOT / "scripts" / "remediation_verify.sh").read_text(encoding="utf-8")
    assert '[ -f "$ROOT/tests/test_c090_c093_production_repro_policy.py" ]' in verify


def test_live_signoff_json_is_gitignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/live_kwork_signoff.json" in ignore
