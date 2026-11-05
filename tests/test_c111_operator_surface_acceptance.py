"""C111: Telegram surfaces errors, unknown_result, needs_context and allowed operator actions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KWORK_INBOX = ROOT / "tests/test_kwork_inbox.py"
DELIVERY_ALERTS = ROOT / "tests/test_delivery_alerts.py"
REVIEW_REISSUE = ROOT / "tests/test_review_reissue.py"
REVIEW_COMMANDS = ROOT / "tests/test_review_commands.py"


def test_c111_evidence_modules_present() -> None:
    assert KWORK_INBOX.is_file()
    assert DELIVERY_ALERTS.is_file()
    assert REVIEW_COMMANDS.is_file()


def _integration_env() -> dict[str, str]:
    env = os.environ.copy()
    env_path = ROOT / ".jobpilot_integration_test.env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key, value)
    env["JOBPILOT_INTEGRATION_TESTS"] = "1"
    return env


def test_c111_alert_and_command_bundle() -> None:
    env = os.environ.copy()
    proc = subprocess.run([sys.executable, "-m", "pytest", str(KWORK_INBOX), str(DELIVERY_ALERTS), str(REVIEW_COMMANDS), "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C111 alert/command bundle failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


def test_c111_reissue_recovery_bundle() -> None:
    env = _integration_env()
    proc = subprocess.run([
            sys.executable,
            "-m",
            "pytest",
            str(REVIEW_REISSUE),
            "-q",
            "-k",
            "delivery_state_cannot_be_bypassed or reissue_racing",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C111 reissue bundle failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
