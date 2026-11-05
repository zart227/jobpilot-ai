"""Markdown triage checklist from outbox inventory (no sends)."""

import os
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops_outbox_triage_checklist.py"
render_markdown = runpy.run_path(str(SCRIPT))["_render_markdown"]


def test_render_markdown_includes_groups_and_checkboxes():
    report = {
        "database_name": "jobpilot_ai",
        "needs_attention_total": 2,
        "needs_attention_triage_groups": [
            {
                "kind": "proposal_approval",
                "error_bucket": "timeout",
                "count": 1,
                "operator_hint": "Use rereview.",
                "items": [
                    {
                        "outbox_id": "uuid-1",
                        "reference_id": "ref-1",
                        "telegram_message_id": 99,
                        "created_at": "2026-11-02T00:00:00+00:00",
                    }
                ],
                "items_truncated": 0,
            }
        ],
    }
    text = render_markdown(report)
    assert "proposal_approval / timeout" in text
    assert "- [ ] outbox `uuid-1`" in text
    assert "Use rereview." in text


def test_checklist_requires_database_url(monkeypatch):
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
