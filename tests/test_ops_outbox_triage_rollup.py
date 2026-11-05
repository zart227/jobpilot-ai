import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops_outbox_triage_rollup.py"


def test_triage_rollup_requires_working_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JOBPILOT_WORKING_DATABASE_URL", raising=False)
    result = subprocess.run([sys.executable, str(SCRIPT), "--json"],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
def test_triage_rollup_json_ok(monkeypatch):
    monkeypatch.setenv("JOBPILOT_WORKING_DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setenv("JOBPILOT_ALLOW_TEST_URL", "1")
    result = subprocess.run([sys.executable, str(SCRIPT), "--json"],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert payload.get("ok") is True
    assert "needs_attention_total" in payload
    assert "p2_active_pending_na" in payload
