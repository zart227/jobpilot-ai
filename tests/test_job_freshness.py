import uuid
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.db.models import Job
from app.services.job_freshness import append_decision_audit, stale_generation_reason


def test_stale_generation_reason_by_age() -> None:
    job = Job(
        id=uuid.uuid4(),
        platform="kwork",
        external_id="1",
        title="Old",
        description="d",
        created_at=datetime.now(UTC) - timedelta(hours=200),
    )
    settings = Settings(_env_file=None, JOB_MAX_AGE_HOURS=168)
    assert stale_generation_reason(job, settings) is not None


def test_stale_generation_reason_deadline() -> None:
    job = Job(
        id=uuid.uuid4(),
        platform="kwork",
        external_id="2",
        title="Late",
        description="d",
        created_at=datetime.now(UTC),
        deadline=datetime.now(UTC) - timedelta(days=1),
    )
    settings = Settings(_env_file=None, JOB_SKIP_EXPIRED_DEADLINES=True)
    assert "Дедлайн" in (stale_generation_reason(job, settings) or "")


def test_append_decision_audit_keeps_last_entries() -> None:
    job = Job(
        platform="kwork",
        external_id="3",
        title="T",
        description="d",
        raw_data={},
    )
    for index in range(30):
        append_decision_audit(job, action="filtered", reason=str(index), source="test")
    audit = (job.raw_data or {}).get("decision_audit") or []
    assert len(audit) == 25
    assert audit[-1]["reason"] == "29"
