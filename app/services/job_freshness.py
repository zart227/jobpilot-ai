"""Skip expensive pipeline steps on stale or expired jobs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.config import Settings, get_settings
from app.db.models import Job


def stale_generation_reason(job: Job, settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    created = job.created_at
    if created is not None:
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age_hours = (now - created).total_seconds() / 3600.0
        if age_hours > settings.job_max_age_hours:
            return f"Заказ в базе дольше {settings.job_max_age_hours} ч — генерация отклика пропущена"

    if settings.job_skip_expired_deadlines and job.deadline is not None:
        deadline = job.deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if deadline < now:
            return "Дедлайн заказа уже прошёл — генерация отклика пропущена"
    return None


def append_decision_audit(
    job: Job,
    *,
    action: str,
    reason: str,
    source: str,
) -> None:
    raw = dict(job.raw_data or {})
    log = list(raw.get("decision_audit") or [])
    log.append(
        {
            "at": datetime.now(UTC).isoformat(),
            "action": action,
            "reason": reason,
            "source": source,
        }
    )
    raw["decision_audit"] = log[-25:]
    job.raw_data = raw


async def skip_if_stale(session, job_id: uuid.UUID) -> str | None:
    job = await session.get(Job, job_id)
    if not job:
        return "job_not_found"
    reason = stale_generation_reason(job)
    if reason:
        append_decision_audit(job, action="skip_generation", reason=reason, source="job_freshness")
    return reason
