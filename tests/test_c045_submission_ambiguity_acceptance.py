"""C045: ambiguous post-submit failures finalize as unknown_result."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBMISSION = ROOT / "tests/test_submission_ambiguity.py"
ATOMICITY = ROOT / "tests/test_send_attempt_atomicity.py"


def test_c045_evidence_modules_present() -> None:
    assert SUBMISSION.is_file()
    assert ATOMICITY.is_file()


def test_c045_submission_ambiguity_bundle() -> None:
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
    for path in (SUBMISSION, ATOMICITY):
        proc = subprocess.run([sys.executable, "-m", "pytest", str(path), "-q"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
    )
        if proc.returncode != 0:
            raise AssertionError(
                f"C045 bundle failed for {path.name} (exit {proc.returncode}):\n"
                f"{proc.stdout}\n{proc.stderr}"
            )
