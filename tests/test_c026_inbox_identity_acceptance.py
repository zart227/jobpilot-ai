"""C026: durable inbound identity — stable keys, legacy match, concurrent capture."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

IDENTITY = ROOT / "tests/test_kwork_inbox_identity.py"
CONCURRENT = ROOT / "tests/test_inbox_concurrent_capture.py"


def test_c026_evidence_modules_present() -> None:
    assert IDENTITY.is_file()
    assert CONCURRENT.is_file()


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


def test_c026_inbox_identity_bundle() -> None:
    env = _integration_env()
    proc = subprocess.run([sys.executable, "-m", "pytest", str(IDENTITY), str(CONCURRENT), "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C026 bundle failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
