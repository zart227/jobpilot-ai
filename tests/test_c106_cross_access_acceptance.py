"""C106: cross-cutting unauthorized access — API + Telegram evidence catalog."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

C106_ACCESS_EVIDENCE = (
    ("api_fail_closed_routes", "tests/test_access_regressions.py"),
    ("api_auth_unit", "tests/test_api_auth.py"),
    ("telegram_admin_identity", "tests/test_telegram_access.py"),
    ("telegram_unauthorized_actions", "tests/test_c010_telegram_unauthorized_actions.py"),
    ("callback_stale_binding", "tests/test_c012_callback_fsm_acceptance.py"),
    ("runtime_api_probe", "scripts/c106_runtime_api_probe.sh"),
)


def test_c106_access_evidence_catalog_is_complete():
    assert len(C106_ACCESS_EVIDENCE) >= 6
    for _name, rel in C106_ACCESS_EVIDENCE:
        assert (ROOT / rel).is_file()


def test_c106_data_routes_fail_closed_without_token():
    """Same matrix as test_access_regressions — duplicate guard for C106 acceptance."""
    from fastapi.testclient import TestClient

    from app.config import Settings, get_settings
    from app.db.session import get_db
    from app.main import app

    cfg = Settings(_env_file=None, API_AUTH_REQUIRED=False, API_AUTH_TOKEN="", API_HOST="0.0.0.0")

    async def database():
        yield None

    app.dependency_overrides[get_settings] = lambda: cfg
    app.dependency_overrides[get_db] = database
    client = TestClient(app)
    try:
        for method, path in (
            ("get", "/jobs"),
            ("post", "/scrape/trigger"),
            ("get", "/stats"),
        ):
            response = client.request(method, path)
            assert response.status_code in (401, 503), f"{method} {path} -> {response.status_code}"
        assert client.get("/health").status_code == 200
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_db, None)
        get_settings.cache_clear()
