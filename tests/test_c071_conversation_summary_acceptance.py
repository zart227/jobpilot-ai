"""C071: conversation summary thread scope and failure fallback."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_TESTS = ROOT / "tests/test_conversation_summary.py"


def test_c071_evidence_module_present() -> None:
    assert SUMMARY_TESTS.is_file()


def test_c071_conversation_summary_regression_bundle() -> None:
    proc = subprocess.run([sys.executable, "-m", "pytest", str(SUMMARY_TESTS), "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C071 summary bundle failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )


def test_conversation_summary_thread_scope_from_kwork_mids() -> None:
    from uuid import uuid4

    from app.services.kwork_conversation import conversation_summary_thread_scope

    job_id = uuid4()
    scope = conversation_summary_thread_scope(
        channel="kwork",
        job_id=job_id,
        thread_messages=[
            {"MID": "100", "message": "a", "time": 1},
            {"MID": "200", "message": "b", "time": 2},
        ],
    )
    assert scope == "kwork:thread:100:200:2"

    fallback = conversation_summary_thread_scope(
        channel="kwork",
        job_id=job_id,
        client_message_id="mid-42",
    )
    assert fallback == "kwork:client_mid:mid-42"
