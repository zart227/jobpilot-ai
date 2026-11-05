"""C027: LLM/generation failure preserves durable inbound and enables retry."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CHAT_ERRORS = ROOT / "tests/test_inbox_chat_pipeline_errors.py"
POLLER_DRILL = ROOT / "tests/test_inbox_poller_restart_drill.py"
KWORK_OUTAGE = (
    ROOT / "tests/test_inbox_generation_claims.py::test_kwork_api_error_on_get_messages_skips_dialog_without_aborting_poll"
)


def test_c027_evidence_modules_present() -> None:
    assert CHAT_ERRORS.is_file()
    assert POLLER_DRILL.is_file()


def _integration_env() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location(
        "integration_subprocess",
        ROOT / "tests" / "integration_subprocess.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = mod.load_integration_subprocess_env()
    if env is None:
        pytest.skip("integration test DATABASE_URL/REDIS_URL not configured (CI unit job)")
    return env


def test_c027_llm_failure_and_retry_bundle() -> None:
    env = _integration_env()
    proc = subprocess.run([sys.executable, "-m", "pytest", str(CHAT_ERRORS), str(POLLER_DRILL), "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C027 chat/retry bundle failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


def test_c027_kwork_outage_preserves_other_dialogs() -> None:
    env = _integration_env()
    proc = subprocess.run([sys.executable, "-m", "pytest", str(KWORK_OUTAGE), "-q"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"C027 Kwork outage drill failed (exit {proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
