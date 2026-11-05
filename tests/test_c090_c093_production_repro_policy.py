"""C090/C093: lockfile-based Docker install and production compose profile."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
PROD = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
VERIFY_LOCK = (ROOT / "scripts" / "verify_lockfile.sh").read_text(encoding="utf-8")


def test_dockerfile_installs_from_requirements_lock() -> None:
    assert "requirements-lock.txt" in DOCKERFILE
    assert "pip install -r requirements-lock.txt" in DOCKERFILE
    assert "pip install --no-deps -e ." in DOCKERFILE


def test_verify_lockfile_matches_docker_install_pattern() -> None:
    assert "install -r requirements-lock.txt" in VERIFY_LOCK
    assert "install --no-deps -e ." in VERIFY_LOCK


def test_dockerfile_runs_as_non_root() -> None:
    assert re.search(r"USER\s+1000:1000", DOCKERFILE)


def test_prod_compose_api_has_no_reload() -> None:
    api_block = PROD.split("    api:", 1)[1].split("\n    celery-worker:", 1)[0]
    assert "--reload" not in api_block
    assert "APP_ENV: production" in api_block


def test_prod_compose_app_services_only_mount_data() -> None:
    for service in ("api:", "celery-worker:", "celery-beat:", "telegram-bot:"):
        section = PROD.split(f"    {service}", 1)[1]
        block = section.split("\n    ", 1)[0]
        assert "!override" in block or "./data:/app/data" in PROD
        assert "./app:" not in PROD


def test_merged_prod_compose_has_no_app_source_bind_mounts() -> None:
    if not shutil.which("docker"):
        pytest.skip("docker CLI not available")
    env_path = ROOT / ".env"
    created_env = False
    if not env_path.is_file():
        env_path.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        created_env = True
    bridge = ROOT / "data" / "cursor_bridge.env"
    created_bridge = False
    if not bridge.is_file():
        bridge.parent.mkdir(parents=True, exist_ok=True)
        bridge.write_text("# CI/prod compose config probe\n", encoding="utf-8")
        created_bridge = True
    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "docker-compose.yml",
                "-f",
                "docker-compose.prod.yml",
                "config",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        if created_env:
            env_path.unlink(missing_ok=True)
        if created_bridge:
            bridge.unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stderr
    assert "source: ./app" not in proc.stdout
    assert "target: /app/app" not in proc.stdout
