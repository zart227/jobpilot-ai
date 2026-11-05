import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops_remediation_snapshot.py"


def test_snapshot_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JOBPILOT_WORKING_DATABASE_URL", raising=False)
    result = subprocess.run([sys.executable, str(SCRIPT)],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
def test_snapshot_json_shape(monkeypatch):
    monkeypatch.setenv("JOBPILOT_WORKING_DATABASE_URL", os.environ["DATABASE_URL"])
    result = subprocess.run([sys.executable, str(SCRIPT), "--sample-limit", "3"],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    import json
    import re

    payload = json.loads(re.search(r"\{.*\}", result.stdout, re.DOTALL).group())
    assert "embedding" in payload and "outbox" in payload
    assert "triage_groups" in payload["outbox"]
    assert "pending_outbox" in payload
    assert "inactive_needs_attention_orphan_count" in payload["pending_outbox"]
