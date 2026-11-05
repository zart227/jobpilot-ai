from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import (
    _drop_invalid_generic_scraper_selectors_env,
    _drop_legacy_shell_database_url,
    get_settings,
)

_drop_legacy_shell_database_url()
_drop_invalid_generic_scraper_selectors_env()
get_settings.cache_clear()


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip integration subprocess bundles in the CI unit job (no test DATABASE_URL)."""
    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") == "1":
        return
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "integration_subprocess",
        Path(__file__).resolve().parent / "integration_subprocess.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if mod.load_integration_subprocess_env() is not None:
        return
    if "bundle" in item.name or "drill" in item.name:
        pytest.skip("integration subprocess bundle requires test DATABASE_URL/REDIS_URL")
    module_file = Path(getattr(item.module, "__file__", "") or "")
    if (
        module_file.name.startswith("test_c")
        and "acceptance" in module_file.name
        and "evidence_modules_present" not in item.name
    ):
        pytest.skip("C acceptance subprocess requires test DATABASE_URL/REDIS_URL")


def pytest_configure(config: pytest.Config) -> None:
    """Integration tests must never use the application's working database/broker."""
    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        return
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    database_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    try:
        database_name = make_url(database_url).database or ""
        redis_database = int(make_url(redis_url).database or "0")
    except (ValueError, ArgumentError) as exc:
        raise pytest.UsageError(
            "Integration tests require explicit test DATABASE_URL/REDIS_URL"
        ) from exc
    if not database_name.endswith("_test") or redis_database < 2:
        raise pytest.UsageError(
            "Integration tests require a database ending in _test and REDIS_URL database >= 2; "
            "the working database and broker are unsafe for tests."
        )
    settings = get_settings()
    for url in (settings.celery_broker_url, settings.celery_result_backend):
        if int(make_url(url).database or "0") < 2:
            raise pytest.UsageError(
                "Integration tests require CELERY_BROKER_URL and CELERY_RESULT_BACKEND "
                "to use Redis databases >= 2."
            )


@pytest.fixture(autouse=True)
def integration_test_database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep integration tests on DATABASE_URL; ops scripts must opt in to WORKING URL."""
    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        yield
        return
    test_url = os.environ.get("DATABASE_URL", "")
    if test_url:
        monkeypatch.setenv("DATABASE_URL", test_url)
    monkeypatch.delenv("JOBPILOT_WORKING_DATABASE_URL", raising=False)
    yield


@pytest.fixture(autouse=True)
def disable_api_rate_limit_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """TestClient hits main app; avoid Redis rate limit coupling in unit tests."""
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def reset_sqlalchemy_engine_after_integration_test() -> None:
    """Avoid asyncpg 'attached to a different loop' when integration tests reuse the pool."""
    yield
    if os.environ.get("JOBPILOT_INTEGRATION_TESTS") != "1":
        return
    from app.db.session import engine

    await engine.dispose()


@pytest.fixture
def live_signoff_artifacts(tmp_path):
    """Owner attestation fixture; DB verification must be mocked separately."""
    import hashlib
    import json
    import uuid

    ui = b"test-only UI evidence fixture"
    (tmp_path / "ui.png").write_bytes(ui)
    digest = hashlib.sha256(b"actual approved text").hexdigest()
    attempt = {
        "send_attempt_id": str(uuid.uuid4()),
        "kwork_order_id": "42",
        "snapshot_sha256": digest,
        "mode": "live",
        "status": "confirmed_sent",
        "dry_run": False,
    }
    content = json.dumps(attempt).encode()
    (tmp_path / "attempt.json").write_bytes(content)
    data = {
        **{k: attempt[k] for k in ("send_attempt_id", "kwork_order_id", "snapshot_sha256")},
        "schema_version": 2,
        "mode": "live",
        "approved_by": "test owner",
        "approved_at": "2026-10-15T10:00:00+03:00",
        "verified_by": "test owner",
        "verified_at": "2026-10-15T10:10:00+03:00",
        "authorization_reference": "test human message 42",
        "snapshot_verified": True,
        "ui_matches_db": True,
        "ui_evidence": {"path": "ui.png", "sha256": hashlib.sha256(ui).hexdigest()},
        "attempt_evidence": {"path": "attempt.json", "sha256": hashlib.sha256(content).hexdigest()},
    }
    path = tmp_path / "signoff.json"
    path.write_text(json.dumps(data))
    return path, data


async def seed_test_kwork_eligibility(session, job_id, *, price=500):
    """Opt in a synthetic Kwork job to the current policy for integration fixtures."""
    import uuid

    from app.db.models import Client, Job
    from app.services.opportunity_policy import kwork_opportunity_fingerprint

    job = await session.get(Job, job_id)
    client = Client(id=uuid.uuid4(), platform='kwork', external_id=str(uuid.uuid4()),
                    metadata_={'projects_posted': 5, 'hire_percent': 50, 'stats_source': 'kwork_buyer_block_v1'})
    session.add(client)
    await session.flush()
    job.client_id = client.id
    facts = {
        'id': str(job.id), 'client_id': str(client.id), 'platform': job.platform, 'title': job.title, 'description': job.description,
        'budget_min': float(job.budget_min) if job.budget_min else None,
        'budget_max': float(job.budget_max) if job.budget_max else None,
        'budget_currency': job.budget_currency, 'client_projects_posted': 5, 'client_hire_percent': 50, 'client_stats_source': 'kwork_buyer_block_v1',
        'url': job.url, 'deadline': job.deadline.isoformat() if job.deadline else None,
        'skills': job.skills or [],
    }
    job.raw_data = {**(job.raw_data or {}), 'opportunity_assessment': {
        'relevant': True, 'assessment_id': 'e48e15f8-6d76-4076-9631-78bf927c5719', 'source_fingerprint': kwork_opportunity_fingerprint(facts),
        'estimated_hours': 2, 'estimated_calendar_days': 1, 'scope_clear': True,
        'complexity': 'simple', 'minimum_price_rub': price,
    }}
    await session.flush()


async def seed_test_proposal_approval(session, job_id, proposal_id):
    """Explicit synthetic approval for isolated tests, never production grandfathering."""
    import uuid

    from app.db.models import Job, Proposal
    from app.services.approval_snapshot import capture_proposal_card, record_proposal_approval

    job = await session.get(Job, job_id)
    proposal = await session.get(Proposal, proposal_id)
    pending_id = uuid.uuid4()
    await capture_proposal_card(session, pending_id, job, proposal)
    await record_proposal_approval(
        session,
        pending_id,
        job,
        proposal,
        actor_user_id=1,
        chat_id=1,
        message_id=1,
    )
    await session.commit()


async def seed_test_inbox_approval(session, pending_id):
    """Explicit isolated synthetic presentation/approval; status is not authorization."""
    from app.db.models import TelegramInboxPending
    from app.services.inbox_approval import capture_inbox_presentation, record_inbox_approval

    pending = await session.get(TelegramInboxPending, pending_id)
    pending.message_id = 1
    await capture_inbox_presentation(session, pending)
    await record_inbox_approval(
        session, pending, actor_user_id=1, chat_id=pending.chat_id, message_id=1
    )
    await session.commit()
