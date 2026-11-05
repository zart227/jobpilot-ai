"""C036: ambiguous Telegram delivery and duplicate review cards must not re-send to client."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OUTBOX_FENCING = ROOT / "tests/test_outbox_claim_fencing.py"
OUTBOX_RECOVERY = ROOT / "tests/test_outbox_operator_recovery.py"
REVIEW_REISSUE = ROOT / "tests/test_review_reissue.py"
DELIVERY_ALERTS = ROOT / "tests/test_delivery_alerts.py"


def test_c036_evidence_modules_present() -> None:
    assert OUTBOX_FENCING.is_file()
    assert REVIEW_REISSUE.is_file()


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


def test_c036_outbox_ambiguity_bundle() -> None:
    env = _integration_env()
    proc = subprocess.run([sys.executable, "-m", "pytest", str(OUTBOX_FENCING), str(OUTBOX_RECOVERY), "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C036 outbox ambiguity bundle failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


def test_c036_reissue_and_alert_bundle() -> None:
    env = _integration_env()
    proc = subprocess.run([sys.executable, "-m", "pytest", str(REVIEW_REISSUE), str(DELIVERY_ALERTS), "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C036 reissue/alert bundle failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
