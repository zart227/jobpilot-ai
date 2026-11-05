import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "scripts" / "embedding_queue_inventory.py"
CROSSCHECK = ROOT / "scripts" / "ops_pending_outbox_crosscheck.py"


def test_embedding_inventory_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JOBPILOT_WORKING_DATABASE_URL", raising=False)
    result = subprocess.run([sys.executable, str(INVENTORY)],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
def test_embedding_inventory_includes_signed_percent(monkeypatch):
    monkeypatch.setenv("JOBPILOT_WORKING_DATABASE_URL", os.environ["DATABASE_URL"])
    result = subprocess.run([sys.executable, str(INVENTORY)],
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
    assert "jobs_with_current_signature" in payload
    assert "signed_percent_of_total" in payload


def test_crosscheck_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JOBPILOT_WORKING_DATABASE_URL", raising=False)
    result = subprocess.run([sys.executable, str(CROSSCHECK)],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
