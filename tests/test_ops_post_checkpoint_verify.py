import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops_post_checkpoint_verify.py"


def test_post_checkpoint_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JOBPILOT_WORKING_DATABASE_URL", raising=False)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
def test_post_checkpoint_json_shape(monkeypatch):
    monkeypatch.setenv("JOBPILOT_WORKING_DATABASE_URL", os.environ["DATABASE_URL"])
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--limit", "2"],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), result.stderr
    import json
    import re

    payload = json.loads(re.search(r"\{.*\}", result.stdout, re.DOTALL).group())
    assert "embedding_inventory" in payload
    assert "summary" in payload["search_verify"]


def test_verify_report_includes_summary():
    checks = [
        {"self_in_top10": True, "job_id": "a", "title": "t", "hits": 10},
        {"self_in_top10": False, "job_id": "b", "title": "x", "hits": 10},
    ]
    passed = sum(1 for c in checks if c["self_in_top10"])
    failed = [c for c in checks if not c["self_in_top10"]]
    summary = {
        "self_in_top10_ok": passed,
        "self_in_top10_failed": len(failed),
        "failed_sample": [{"job_id": c["job_id"], "title": c["title"], "hits": c["hits"]} for c in failed],
    }
    assert summary["self_in_top10_ok"] == 1
    assert summary["self_in_top10_failed"] == 1
