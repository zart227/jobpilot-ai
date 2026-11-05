import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops_rollout_readiness.py"


def test_rollout_readiness_requires_working_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JOBPILOT_WORKING_DATABASE_URL", raising=False)
    result = subprocess.run([sys.executable, str(SCRIPT)],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT), "JOBPILOT_SKIP_COMPOSE_CHECKS": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_celery_beat_includes_outbox_delivery():
    from app.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule or {}
    assert "deliver-notification-outbox" in schedule
    assert schedule["deliver-notification-outbox"]["task"] == (
        "app.tasks.scrape_tasks.deliver_notification_outbox"
    )


def test_readiness_issues_when_alembic_behind_head():
    from scripts.ops_rollout_readiness import _readiness_issues_from_checks

    issues = _readiness_issues_from_checks(
        working={"ok": True},
        http={"ready_body_ok": True},
        beat={"deliver-notification-outbox": {"task": "x"}},
        alembic={
            "skipped": False,
            "ok": False,
            "at_head": False,
            "alembic_version": "20261102_0008",
            "expected_head": "20261103_0011",
        },
        metrics={"notification_outbox": {"needs_attention": 3}},
        stats={
            "skipped": False,
            "http_status": 200,
            "notification_outbox_needs_attention": 3,
        },
    )
    assert any("alembic head mismatch" in item for item in issues)
    assert not any("needs_attention mismatch" in item for item in issues)


def test_readiness_issues_when_stats_skipped():
    from scripts.ops_rollout_readiness import _readiness_issues_from_checks

    issues = _readiness_issues_from_checks(
        working={"ok": True},
        http={"ready_body_ok": True},
        beat={"deliver-notification-outbox": {"task": "x"}},
        alembic={"skipped": False, "ok": True, "at_head": True, "alembic_version": "h", "expected_head": "h"},
        metrics={"notification_outbox": {"needs_attention": 0}},
        stats={"skipped": True, "reason": "API_AUTH_TOKEN not set"},
    )
    assert any("stats_api_compare skipped" in item for item in issues)


def test_readiness_issues_when_stats_counts_differ():
    from scripts.ops_rollout_readiness import _readiness_issues_from_checks

    issues = _readiness_issues_from_checks(
        working={"ok": True},
        http={"ready_body_ok": True},
        beat={"deliver-notification-outbox": {"task": "x"}},
        alembic={"skipped": False, "ok": True, "at_head": True, "alembic_version": "h", "expected_head": "h"},
        metrics={"notification_outbox": {"needs_attention": 5}},
        stats={
            "skipped": False,
            "http_status": 200,
            "notification_outbox_needs_attention": 2,
        },
    )
    assert any("needs_attention mismatch" in item for item in issues)


def test_stats_compare_reads_ops_notification_outbox(monkeypatch):
    import asyncio

    from scripts import ops_rollout_readiness as mod

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "ops": {
                    "notification_outbox": {
                        "needs_attention": 7,
                        "pending_or_claimed": 1,
                    }
                }
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, headers=None):
            assert headers and headers["Authorization"].startswith("Bearer ")
            return FakeResponse()

    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    result = asyncio.run(mod._stats_compare("http://example.test"))
    assert result == {
        "skipped": False,
        "http_status": 200,
        "notification_outbox_needs_attention": 7,
        "notification_outbox_pending_or_claimed": 1,
    }


@pytest.mark.skipif(os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1", reason="isolated DB required")
def test_rollout_readiness_json_shape(monkeypatch):
    monkeypatch.setenv("JOBPILOT_WORKING_DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.setenv("JOBPILOT_ALLOW_TEST_URL", "1")
    monkeypatch.setenv("JOBPILOT_SKIP_COMPOSE_CHECKS", "1")

    result = subprocess.run([sys.executable, str(SCRIPT), "--base-url", "http://127.0.0.1:8000"],
        check=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)
    assert "celery_beat_schedule" in payload
    assert "ops_metrics" in payload
    assert payload["celery_beat_schedule"].get("deliver-notification-outbox")
