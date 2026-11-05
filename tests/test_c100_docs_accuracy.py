"""C100: README and .env.example match ports, auth, pause settings, and remediation status."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "remediation-plan.md"
README = ROOT / "README.md"
ENV_EXAMPLE = ROOT / ".env.example"


def _checklist_counts() -> tuple[int, int]:
    text = CHECKLIST.read_text(encoding="utf-8")
    header = text.split("## 0.")[0]
    closed_m = re.search(r"\*\*(\d+)/115", header)
    open_m = re.search(r"(\d+)\s+открыто", header)
    assert closed_m and open_m, "checklist header must document X/115 and Y открыто"
    return int(closed_m.group(1)), int(open_m.group(1))


def test_readme_status_matches_checklist_header() -> None:
    closed, opened = _checklist_counts()
    readme = README.read_text(encoding="utf-8")
    intro = readme.split("## Architecture", 1)[0]
    assert f"{closed}/115" in intro
    assert re.search(rf"\*?\*?{opened}\*?\*?\s+открыто", intro) or f"{opened} open" in intro.lower()
    assert "не завершена" in intro.lower() or "not complete" in intro.lower()


def test_readme_does_not_claim_full_remediation_or_release() -> None:
    readme = README.read_text(encoding="utf-8").lower()
    assert "115/115" not in readme
    assert "0 open" not in readme
    assert "release signoff" not in readme or "отсутствует" in readme or "not" in readme


def test_readme_documents_postgres_host_port_and_api_auth() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "5434" in readme
    assert "127.0.0.1:5434" in readme
    assert "API_AUTH_TOKEN" in readme
    assert "TELEGRAM_ADMIN_USER_ID" in readme
    assert "Authorization" in readme or "Bearer" in readme


def test_readme_verify_section_does_not_use_stale_pytest_count() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "176 pytest" not in readme


def test_env_example_documents_database_auth_pause_and_dry_run() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "127.0.0.1:5434" in text
    assert "API_AUTH_TOKEN" in text
    assert "PLATFORM_DRY_RUN" in text
    assert "TELEGRAM_ADMIN_USER_ID" in text
    assert "KWORK_PAUSE_ENABLED" in text
    assert "POSTGRES_PASSWORD" in text
    assert "SQLALCHEMY_ECHO" in text
