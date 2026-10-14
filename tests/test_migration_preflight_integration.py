"""Preflight on CI database after alembic upgrade (no duplicate legacy data)."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="set JOBPILOT_INTEGRATION_TESTS=1",
)


@pytest.mark.asyncio
async def test_preflight_reports_no_blocking_issues_on_clean_db() -> None:
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "migration_preflight.py"
    spec = importlib.util.spec_from_file_location("migration_preflight", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    report = await mod.run_preflight()
    assert isinstance(report, dict)
    for name, rows in report.items():
        assert rows == [], f"unexpected preflight rows for {name}: {rows[:3]}"
