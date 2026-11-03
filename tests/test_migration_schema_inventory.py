"""Schema inventory script on migrated integration DB."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1",
    reason="isolated DB required",
)


async def test_schema_inventory_reports_memory_queue_on_test_db(monkeypatch):
    import importlib.util
    from pathlib import Path

    monkeypatch.setenv("JOBPILOT_ALLOW_TEST_URL", "1")
    script = Path(__file__).resolve().parents[1] / "scripts" / "migration_schema_inventory.py"
    spec = importlib.util.spec_from_file_location("migration_schema_inventory", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    url = os.environ["DATABASE_URL"]
    report = await mod.run_inventory(url)
    assert report["memory_index_pending_table"] is True
    assert report["alembic_version"] == mod.EXPECTED_HEAD
    assert report["ready_after_upgrade"] is True
