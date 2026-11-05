"""C041: offline Kwork offer form DOM contract (not live marketplace HTML)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOM_TESTS = ROOT / "tests/test_kwork_form_dom.py"
FROZEN = ROOT / "tests/test_frozen_submission.py"
APPROVAL_SNAPSHOT = ROOT / "tests/test_approval_snapshot.py"


def test_c041_evidence_module_present() -> None:
    assert DOM_TESTS.is_file()
    fixture = ROOT / "tests/fixtures/kwork/offer_form_synthetic.html"
    assert fixture.is_file()
    assert "Synthetic offline" in fixture.read_text(encoding="utf-8")


def test_c041_frozen_payload_and_readback_bundle() -> None:
    proc = subprocess.run([sys.executable, "-m", "pytest", str(FROZEN), str(APPROVAL_SNAPSHOT), "-q"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C041 frozen/snapshot bundle failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


@pytest.mark.skipif(
    os.environ.get("JOBPILOT_BROWSER_TESTS") != "1",
    reason="offline Chromium opt-in required for C041 DOM bundle",
)
def test_c041_kwork_form_dom_regression_bundle() -> None:
    proc = subprocess.run([sys.executable, "-m", "pytest", str(DOM_TESTS), "-q"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C041 DOM bundle failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
