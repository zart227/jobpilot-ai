"""C025: processing / approval / delivery status transition guards."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_c025_status_transition_regression_bundle() -> None:
    proc = subprocess.run([
            sys.executable,
            "-m",
            "pytest",
            str(ROOT / "tests/test_status_transitions.py"),
            str(ROOT / "tests/test_workflow_states.py"),
            str(ROOT / "tests/test_outcome_states.py"),
            "-q",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C025 bundle failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
