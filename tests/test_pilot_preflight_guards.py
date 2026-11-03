"""Live preflight cannot succeed with unresolved failures."""
import importlib.util
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

spec = importlib.util.spec_from_file_location('preflight', Path(__file__).resolve().parents[1] / 'scripts/ops_step6_preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


@pytest.fixture(autouse=True)
def isolated_database_environment(monkeypatch):
    # preflight.main mutates DATABASE_URL; restore it even when run in the full suite.
    test_url = os.environ.get('DATABASE_URL', 'postgresql+asyncpg://test:test@127.0.0.1:9/preflight_test')
    monkeypatch.setenv('DATABASE_URL', test_url)
    monkeypatch.setattr(preflight, 'WORKING_DB', test_url)


@pytest.mark.parametrize(('pilot', 'unknown', 'qdrant_ok', 'drill_exit', 'expected'), [
    (0, 0, True, 0, 0), (1, 0, True, 0, 1), (0, 3, True, 0, 1),
    (0, None, True, 0, 1), (0, 0, False, 0, 1), (0, 0, True, 1, 1),
])
def test_preflight_fails_closed(monkeypatch, pilot, unknown, qdrant_ok, drill_exit, expected):
    import app.services.kwork_pause as pause

    monkeypatch.setattr(preflight, '_run_pilot_status', lambda: pilot)
    monkeypatch.setattr(preflight, '_unknown_result_note', AsyncMock(return_value={'count': unknown}))
    monkeypatch.setattr(preflight, '_signoff_gate', lambda: {'present': True, 'validator_exit': 0})
    monkeypatch.setattr(preflight, '_qdrant_drill_dry', lambda: {'qdrant': {'ok': qdrant_ok}, 'exit_code': drill_exit})
    monkeypatch.setattr(preflight, '_kwork_access_gate', lambda: {'ok': True, 'browser_auth_verified': True})
    monkeypatch.setattr(pause, 'is_kwork_offers_paused', lambda: False)
    assert preflight.main() == expected


def test_listing_access_without_browser_auth_cannot_authorize_live(monkeypatch):
    import app.services.kwork_pause as pause

    monkeypatch.setattr(preflight, '_run_pilot_status', lambda: 0)
    monkeypatch.setattr(preflight, '_unknown_result_note', AsyncMock(return_value={'count': 0}))
    monkeypatch.setattr(preflight, '_signoff_gate', lambda: {'present': True, 'validator_exit': 0})
    monkeypatch.setattr(preflight, '_qdrant_drill_dry', lambda: {'qdrant': {'ok': True}, 'exit_code': 0})
    monkeypatch.setattr(preflight, '_kwork_access_gate', lambda: {'ok': True, 'browser_auth_verified': False})
    monkeypatch.setattr(pause, 'is_kwork_offers_paused', lambda: False)
    assert preflight.main() == 1
