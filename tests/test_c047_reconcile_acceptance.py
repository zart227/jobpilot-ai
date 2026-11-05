"""C047: reconciliation strict matching and manual path for live proposals."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECONCILE = ROOT / "tests/test_send_attempt_reconcile.py"


def test_c047_evidence_module_present() -> None:
    assert RECONCILE.is_file()


def test_c047_reconcile_regression_bundle() -> None:
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
    proc = subprocess.run([sys.executable, "-m", "pytest", str(RECONCILE), "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C047 bundle failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
