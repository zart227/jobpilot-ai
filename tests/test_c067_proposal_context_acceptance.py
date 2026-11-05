"""C067: chat context uses confirmed non-dry-run proposal send snapshot."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

C067_EVIDENCE = ROOT / "tests/test_chat_confirmed_proposal_context.py"


def test_c067_evidence_module_present() -> None:
    assert C067_EVIDENCE.is_file()


@pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated DB required for C067 regression bundle",
)
def test_c067_confirmed_proposal_context_regression_bundle() -> None:
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
    proc = subprocess.run([
            sys.executable,
            "-m",
            "pytest",
            str(C067_EVIDENCE),
            "-q",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C067 bundle failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
