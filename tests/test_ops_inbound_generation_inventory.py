import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops_inbound_generation_inventory.py"
_spec = importlib.util.spec_from_file_location("ops_inbound_generation_inventory", SCRIPT)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
_attempts = _module._attempts


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (None, 0),
        ({}, 0),
        ({"generation_attempts": 3}, 3),
        ({"generation_attempts": "5"}, 5),
        ({"generation_attempts": "bad"}, 0),
    ],
)
def test_generation_attempts_parser(meta, expected):
    assert _attempts(meta) == expected


def test_inventory_requires_database_url(monkeypatch):
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
def test_inventory_json_shape(monkeypatch):
    monkeypatch.setenv("JOBPILOT_WORKING_DATABASE_URL", os.environ["DATABASE_URL"])
    result = subprocess.run([sys.executable, str(SCRIPT)],
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
    assert "kwork_inbound_by_status" in payload
    assert "ops_alert_totals_by_status" in payload
