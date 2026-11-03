"""C015: secrets must not live in git; DB password comes from configuration."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
GITIGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")


def test_env_and_live_signoff_gitignored() -> None:
    assert ".env" in GITIGNORE or ".env\n" in GITIGNORE
    assert "data/live_kwork_signoff.json" in GITIGNORE
    assert "data/cursor_bridge.env" in GITIGNORE


def test_compose_postgres_password_from_env() -> None:
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-jobpilot_secret}" in COMPOSE


def test_compose_database_url_uses_postgres_password_variable() -> None:
    assert "${POSTGRES_PASSWORD:-jobpilot_secret}" in COMPOSE
    assert "DATABASE_URL: postgresql+asyncpg://jobpilot:${POSTGRES_PASSWORD" in COMPOSE


def test_env_example_documents_postgres_password_override() -> None:
    assert "POSTGRES_PASSWORD" in ENV_EXAMPLE


def test_secret_scan_repo_passes() -> None:
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/secret_scan_repo.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_tracked_files_do_not_contain_real_telegram_token_pattern() -> None:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    token = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")
    for rel in proc.stdout.splitlines():
        path = ROOT / rel
        if not path.is_file() or path.suffix in {".png", ".jpg", ".lock"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if token.search(text) and "secret_scan_repo" not in rel:
            raise AssertionError(f"possible telegram token in tracked file: {rel}")
