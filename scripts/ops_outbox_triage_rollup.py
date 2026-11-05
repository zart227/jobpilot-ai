"""Run all read-only outbox operator triage CLIs (stdout summaries)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")
if not os.path.isfile(PY):
    PY = sys.executable


def _env() -> dict[str, str]:
    from scripts.ops_database_url import pick_ops_database_url

    working = pick_ops_database_url()
    if not working:
        print("Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)
    env = {**os.environ, "PYTHONPATH": ROOT, "DATABASE_URL": working}
    env.pop("JOBPILOT_WORKING_DATABASE_URL", None)
    return env


def _run_json(script: str, *args: str) -> dict:
    proc = subprocess.run([PY, os.path.join(ROOT, "scripts", script), *args],
        cwd=ROOT,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr or proc.stdout or script)
    from scripts.ops_json_cli import parse_json_stdout

    return parse_json_stdout(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit combined JSON rollup")
    args = parser.parse_args()
    pre = _run_json("ops_working_db_preflight.py")
    if not pre.get("ok"):
        print(json.dumps({"ok": False, "preflight": pre}, ensure_ascii=False, indent=2))
        return 1
    cross = _run_json("ops_pending_outbox_crosscheck.py")
    inv = _run_json("ops_notification_outbox_inventory.py", "--pending-limit", "50")
    p1t = _run_json("ops_outbox_p1_timeout_checklist.py", "--json")
    p1o = _run_json("ops_outbox_p1_timeout_checklist.py", "--json", "--error-bucket", "other")
    p2 = _run_json("ops_outbox_p2_active_pending_checklist.py", "--json")
    inbox = _run_json("ops_outbox_inbox_na_checklist.py", "--json", "--pending-limit", "50")
    rollup = {
        "ok": True,
        "database_name": pre.get("database_name"),
        "needs_attention_total": inv.get("needs_attention_total"),
        "needs_attention_by_reference_type": inv.get("needs_attention_by_reference_type"),
        "inactive_orphan": cross.get("inactive_needs_attention_orphan_count"),
        "inactive_by_bucket": cross.get("inactive_needs_attention_by_error_bucket"),
        "p1_timeout_inactive": p1t.get("inactive_orphan_count"),
        "p1_other_inactive": p1o.get("inactive_orphan_count"),
        "p2_active_pending_na": p2.get("active_pending_with_outbox_needs_attention"),
        "inbox_alert_na_refs": inbox.get("inbox_alert_needs_attention_by_reference_type"),
    }
    if args.json:
        print(json.dumps(rollup, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(rollup, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
