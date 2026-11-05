import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/generate_acceptance_signoff.py"


def test_generator_refuses_working_database_before_connecting():
    env = dict(os.environ, JOBPILOT_INTEGRATION_TESTS="1",
               DATABASE_URL="postgresql+asyncpg://test:unused@127.0.0.1:1/production",
               PLATFORM_DRY_RUN="false")
    result = subprocess.run([sys.executable, str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Refusing to write simulation records" in result.stderr


@pytest.mark.asyncio
async def test_generator_forces_simulation_and_never_claims_human_approval(monkeypatch):
    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        pytest.skip("requires explicitly isolated test DB")
    spec = importlib.util.spec_from_file_location("generate_acceptance_signoff", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("PLATFORM_DRY_RUN", "false")
    # _run forces true before loading Settings. monkeypatch restores it after the test.
    result = await mod._run()
    assert result["mode"] == "simulation"
    assert result["ui_matches_db"] is False
    assert "approved_by" not in result
    assert mod.SIGNOFF.name == "simulation_signoff.json"
    from app.config import get_settings
    get_settings.cache_clear()
