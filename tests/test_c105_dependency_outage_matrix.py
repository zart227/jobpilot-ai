"""C105: map dependency failure/recovery drills to automated evidence (isolated; no production deps)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Each row: dependency, failure/recovery scenario, primary test module (integration or unit).
DEPENDENCY_OUTAGE_EVIDENCE = (
    ("postgresql", "readiness not_ready when PG check fails", "tests/test_health_checks.py"),
    (
        "postgresql",
        "compose stop/start /ready 503→200 (manual drill)",
        "docs/staging-drill-log.md#dependency-failure",
    ),
    ("redis", "rate limit fail-closed on ConnectionError", "tests/test_api_rate_limit.py"),
    (
        "redis",
        "compose stop/start /ready 503→200 (manual drill)",
        "docs/staging-drill-log.md#dependency-failure",
    ),
    ("qdrant", "readiness degraded when Qdrant unavailable", "tests/test_health_checks.py"),
    (
        "qdrant",
        "memory queue retry after failed upsert",
        "tests/test_memory_index_queue.py",
    ),
    (
        "llm",
        "inbox generation survives LLM exception per dialog",
        "tests/test_inbox_generation_claims.py",
    ),
    (
        "llm",
        "inbox transaction rollback on LLM outage mid-batch",
        "tests/test_inbox_transaction_boundaries.py",
    ),
    (
        "telegram",
        "outbox claim fencing on send_message failure",
        "tests/test_outbox_claim_fencing.py",
    ),
    (
        "telegram",
        "submission ambiguity on connection drop",
        "tests/test_submission_ambiguity.py",
    ),
)


def test_c105_dependency_outage_evidence_catalog_covers_required_deps():
    deps = {row[0] for row in DEPENDENCY_OUTAGE_EVIDENCE}
    assert deps == {"postgresql", "redis", "qdrant", "llm", "telegram"}
    for _dep, _scenario, path in DEPENDENCY_OUTAGE_EVIDENCE:
        if path.startswith("docs/"):
            assert (ROOT / path.split("#")[0]).is_file()
        else:
            assert (ROOT / path).is_file()


AUTOMATED_EVIDENCE_PATHS = tuple(
    path for _dep, _scenario, path in DEPENDENCY_OUTAGE_EVIDENCE if not path.startswith("docs/")
)


def test_c105_automated_outage_recovery_evidence_pytest_green():
    """Run every automated C105 evidence module (not manual compose drill log)."""
    import os
    import subprocess
    import sys

    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        pytest.skip("integration DB/Redis required for C105 evidence bundle")

    env = os.environ.copy()
    env_path = ROOT / ".jobpilot_integration_test.env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key, value)
    env["JOBPILOT_INTEGRATION_TESTS"] = "1"
    env.setdefault("API_RATE_LIMIT_ENABLED", "false")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *AUTOMATED_EVIDENCE_PATHS],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
