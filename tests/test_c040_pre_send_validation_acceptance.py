"""C040: pre-send validation of job/proposal binding and approved payload."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BUNDLE = (
    ROOT / "tests/test_inbox_approval_enforcement.py",
    ROOT / "tests/test_send_attempt_atomicity.py",
    ROOT / "tests/test_kwork_opportunity_send_gate.py",
    ROOT / "tests/test_draft_mutation_guards.py",
)


def test_c040_evidence_modules_present() -> None:
    for path in BUNDLE:
        assert path.is_file(), path.name


def test_c040_pre_send_validation_bundle() -> None:
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
    for path in BUNDLE:
        proc = subprocess.run([sys.executable, "-m", "pytest", str(path), "-q"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
    )
        if proc.returncode != 0:
            raise AssertionError(
                f"C040 bundle failed for {path.name} (exit {proc.returncode}):\n"
                f"{proc.stdout}\n{proc.stderr}"
            )
