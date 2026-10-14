#!/usr/bin/env python3
"""Map audit-2026-10-13 findings to remediation evidence (static matrix + file existence check)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Finding:
    num: int
    priority: str
    title: str
    status: str  # addressed | partial | open | policy
    evidence: tuple[str, ...]
    tests: tuple[str, ...] = ()
    notes: str = ""


FINDINGS: tuple[Finding, ...] = (
    Finding(
        1,
        "P1",
        "API auth and exposed ports",
        "addressed",
        ("app/api/auth.py", "app/api/rate_limit.py", "docker-compose.yml"),
        ("tests/test_api_auth.py", "tests/test_api_rate_limit.py"),
    ),
    Finding(
        2,
        "P1",
        "Telegram admin on approval/inbox",
        "addressed",
        ("app/telegram/admin_middleware.py", "app/telegram/access.py"),
        ("tests/test_telegram_access.py",),
    ),
    Finding(
        3,
        "P1",
        "Inbound lost on failure",
        "addressed",
        ("app/db/models.py", "app/services/kwork_inbox.py", "docs/inbox-outbox-policy.md"),
        ("tests/test_acceptance_inbox_flow.py",),
    ),
    Finding(
        4,
        "P1",
        "Duplicate inbox reply send",
        "addressed",
        ("app/services/send_attempt_service.py", "app/services/kwork_inbox.py"),
        ("tests/test_send_attempt_inbox_concurrency.py",),
    ),
    Finding(
        5,
        "P1",
        "Proposal send races",
        "addressed",
        ("app/services/proposal_sender.py", "app/services/send_attempt_service.py"),
        ("tests/test_acceptance_dry_run.py", "tests/test_send_attempt_reconcile.py"),
    ),
    Finding(
        6,
        "P1",
        "Schema UNIQUE/CHECK via Alembic",
        "addressed",
        ("alembic/versions/20261013_0001_jobpilot_schema.py", "scripts/init_db.py"),
        ("tests/test_migration_preflight_integration.py",),
        "Existing DB: run migration_preflight before upgrade.",
    ),
    Finding(
        7,
        "P1",
        "Concurrent pipeline duplicates",
        "addressed",
        ("app/services/job_processing.py", "app/tasks/scrape_tasks.py"),
        ("tests/test_job_processing_claim.py",),
    ),
    Finding(
        8,
        "P1",
        "LLM error treated as filter",
        "addressed",
        ("app/services/job_pipeline.py", "app/schemas/llm_outputs.py"),
        ("tests/test_llm_output_schemas.py",),
    ),
    Finding(
        9,
        "P1",
        "Wrong job in reply context",
        "addressed",
        ("app/services/kwork_job_match.py",),
        ("tests/test_kwork_job_match.py",),
    ),
    Finding(
        10,
        "P1",
        "Proposal notifications not recovered",
        "addressed",
        ("app/services/notification_outbox.py",),
        ("tests/test_notification_outbox_delivery.py",),
    ),
    Finding(
        11,
        "P2",
        "Kwork pause stops all platforms",
        "partial",
        ("app/services/kwork_pause.py",),
        ("tests/test_kwork_pause.py", "tests/test_kwork_pause_policy.py"),
        "Inbox vs scrape split documented; full pause behavior retained by design.",
    ),
    Finding(
        12,
        "P2",
        "Qdrant failure blocks flow",
        "partial",
        ("app/services/health_checks.py", "app/memory/qdrant_store.py"),
        ("tests/test_health_checks.py",),
        "Readiness degrades without Qdrant; pipeline paths vary.",
    ),
    Finding(
        13,
        "P2",
        "Hash vectors not semantic",
        "addressed",
        ("app/llm/embeddings.py", "app/tasks/embedding_tasks.py"),
        (),
        "Full staging reindex drill still manual (runbook).",
    ),
    Finding(
        14,
        "P2",
        "/chat ignores stored conversation",
        "addressed",
        ("app/services/conversation_context_service.py", "app/main.py"),
        ("tests/test_conversation_summary.py",),
    ),
    Finding(
        15,
        "P2",
        "Outcomes API learning gap",
        "addressed",
        ("app/services/outcome_service.py", "app/agents/learning_agent.py"),
        ("tests/test_acceptance_dry_run.py",),
    ),
    Finding(
        16,
        "P2",
        "Duplicate outcomes/rewards",
        "addressed",
        ("alembic/versions/20261013_0004_outcome_idempotency.py", "app/services/outcome_service.py"),
        ("tests/test_outcome_states.py",),
    ),
    Finding(
        17,
        "P2",
        "Learning weights without validation",
        "addressed",
        ("app/services/scoring_weights_validation.py", "scripts/rollback_scoring_weights.py"),
        ("tests/test_scoring_weights_validation.py",),
    ),
    Finding(
        18,
        "P2",
        "Invalid scoring JSON → 50",
        "addressed",
        ("app/schemas/llm_outputs.py", "app/agents/scoring_agent.py"),
        ("tests/test_llm_output_schemas.py",),
    ),
    Finding(
        19,
        "P2",
        "Client merge without external_id",
        "addressed",
        ("app/services/job_pipeline.py",),
        ("tests/test_job_pipeline_client.py",),
    ),
    Finding(
        20,
        "P2",
        "Generic scraper relative URLs",
        "addressed",
        ("app/scrapers/generic.py",),
        ("tests/test_generic_scraper_ids.py",),
    ),
    Finding(
        21,
        "P2",
        "Tests miss critical paths",
        "partial",
        (".github/workflows/ci.yml", "tests/"),
        (),
        "Coverage grown; live Kwork UI not automated.",
    ),
    Finding(
        22,
        "P3",
        "Ops reproducibility",
        "addressed",
        ("requirements-lock.txt", "docker-compose.prod.yml", "scripts/production_rollout.sh"),
        ("tests/test_remediation_checklist_gate.py",),
    ),
    Finding(
        23,
        "P3",
        "Lint duplicates",
        "policy",
        ("pyproject.toml", "app/utils/formatting.py"),
        (),
        "Ruff 0 errors with documented ignores; BLE001 tighten later.",
    ),
)


def _path_ok(rel: str) -> bool:
    p = ROOT / rel
    if rel.endswith("/"):
        return p.is_dir()
    if "*" in rel:
        return any(ROOT.glob(rel))
    return p.exists()


def render_markdown() -> str:
    lines = [
        "# Audit → remediation matrix",
        "",
        "Generated by `scripts/audit_remediation_status.py` from [audit-2026-10-13.md](audit-2026-10-13.md).",
        "",
        "| # | P | Статус | Находка | Evidence OK | Тесты |",
        "| - | - | ------ | ------- | ----------- | ----- |",
    ]
    for f in FINDINGS:
        ev_ok = all(_path_ok(e) for e in f.evidence) if f.evidence else True
        ev_cell = "yes" if ev_ok else "**missing**"
        tests = ", ".join(f"`{t}`" for t in f.tests) if f.tests else "—"
        title = f.title[:48]
        lines.append(
            f"| {f.num} | {f.priority} | {f.status} | {title} | {ev_cell} | {tests} |"
        )
    lines.extend(["", "## Notes per finding", ""])
    for f in FINDINGS:
        if f.notes:
            lines.append(f"- **#{f.num}**: {f.notes}")
    missing = [f for f in FINDINGS if f.evidence and not all(_path_ok(e) for e in f.evidence)]
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Findings: **{len(FINDINGS)}**")
    by_status: dict[str, int] = {}
    for f in FINDINGS:
        by_status[f.status] = by_status.get(f.status, 0) + 1
    lines.append(f"- By status: {by_status}")
    if missing:
        lines.append(f"- **Missing evidence paths:** {len(missing)} finding(s)")
    else:
        lines.append("- All evidence paths present on disk.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help="Write markdown to path (default: stdout only)",
    )
    args = parser.parse_args()
    text = render_markdown()
    if args.write:
        args.write.write_text(text, encoding="utf-8")
        print(f"Wrote {args.write}")
    else:
        print(text)
    missing = [f.num for f in FINDINGS if f.evidence and not all(_path_ok(e) for e in f.evidence)]
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
