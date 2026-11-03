"""Evidence-based eligibility rules for the first-review strategy."""
import hashlib
import json
import math
import re
import uuid

REVIEW_WEIGHTS = {
    "skill_match": 0.25, "budget": 0.05, "complexity": 0.35,
    "client_quality": 0.25, "competition": 0.10,
}


def kwork_opportunity_fingerprint(job: dict) -> str:
    """Bind an assessment to the job and buyer facts actually supplied to the filter."""
    fields = (
        "id", "title", "description", "budget_min", "budget_max", "budget_currency", "url", "deadline",
        "client_id", "client_projects_posted", "client_hire_percent", "client_stats_source",
        "client_evidence_revision", "listing_revision",
    )
    values = {key: job.get(key) for key in fields}
    values["skills"] = job.get("skills") or []
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def kwork_assessment_rejection(job: dict, assessment: object) -> str | None:
    """Reject stale, legacy, incomplete or ineligible Kwork assessments before sending."""
    if job.get("platform") != "kwork":
        return None
    if client_rejection(job, 0, 0):
        return "buyer_not_eligible"
    if not isinstance(assessment, dict) or assessment.get("relevant") is not True:
        return "assessment_required"
    try:
        revision = assessment.get("assessment_id")
        if not isinstance(revision, str) or str(uuid.UUID(revision)) != revision:
            return "assessment_required"
    except (ValueError, AttributeError):
        return "assessment_required"
    if assessment.get("source_fingerprint") != kwork_opportunity_fingerprint(job):
        return "assessment_stale"
    hours, days = assessment.get("estimated_hours"), assessment.get("estimated_calendar_days")
    if (
        type(hours) not in (int, float) or not math.isfinite(hours) or not 0 < hours <= 3
        or type(days) not in (int, float) or not math.isfinite(days) or not 0 < days <= 1
        or assessment.get("scope_clear") is not True or assessment.get("complexity") != "simple"
    ):
        return "assessment_ineligible"
    price = assessment.get("minimum_price_rub")
    if type(price) is not int or price < 500:
        return "assessment_ineligible"
    cap = job.get("budget_max")
    if cap is not None and price > cap:
        return "assessment_ineligible"
    return None


async def kwork_job_facts(session, job) -> dict:
    from app.db.models import Client

    client = await session.get(Client, job.client_id) if job.client_id else None
    metadata = (client.metadata_ or {}) if client else {}
    return {
        "id": str(job.id), "platform": job.platform, "title": job.title,
        "description": job.description, "budget_min": float(job.budget_min) if job.budget_min else None,
        "budget_max": float(job.budget_max) if job.budget_max else None,
        "budget_currency": job.budget_currency,
        "url": job.url, "deadline": job.deadline.isoformat() if job.deadline else None,
        "skills": job.skills or [],
        "client_id": str(job.client_id) if job.client_id else None,
        "client_name": client.name if client else None,
        "client_rating": float(client.rating) if client and client.rating is not None else None,
        "client_projects_posted": metadata.get("projects_posted"),
        "client_hire_percent": metadata.get("hire_percent"),
        "client_stats_source": metadata.get("stats_source"),
        "client_evidence_revision": metadata.get("_evidence_revision"),
        "listing_revision": (job.raw_data or {}).get("listing_revision"),
    }


async def kwork_job_assessment_rejection(session, job) -> str | None:
    if job.platform != "kwork":
        return None
    facts = await kwork_job_facts(session, job)
    return kwork_assessment_rejection(facts, (job.raw_data or {}).get("opportunity_assessment"))


def client_rejection(job: dict, minimum_rating: float, minimum_paid: int) -> str | None:
    if job.get("platform") == "kwork":
        if job.get("client_stats_source") != "kwork_buyer_block_v1":
            return "Статистика заказчика не подтверждена проверенным блоком карточки"
        projects = job.get("client_projects_posted")
        hired = job.get("client_hire_percent")
        if type(projects) is not int or type(hired) is not int:
            return "Нет подтверждённых данных карточки заказчика о проектах и найме"
        if projects < 5 or not 50 <= hired <= 100:
            return "Заказчик не соответствует порогу: от 5 проектов и найм от 50%"
        return None
    rating = job.get("client_rating")
    paid = job.get("client_completed_paid_orders")
    try:
        rating = float(rating)
        paid = int(paid)
    except (TypeError, ValueError, OverflowError):
        return "Не подтверждены рейтинг заказчика и выполненные оплаченные заказы"
    if not math.isfinite(rating) or not minimum_rating <= rating <= 5:
        return f"Рейтинг заказчика ниже {minimum_rating}/5 или не подтверждена шкала"
    if paid < minimum_paid:
        return f"Нет подтверждения минимум {minimum_paid} выполненного оплаченного заказа"
    return None


def parse_buyer_history(text: str) -> dict:
    """Only explicit buyer labels count; seller reviews and closed projects do not."""
    text = text.replace("\xa0", " ")
    rating = re.search(r"Рейтинг\s+(?:покупателя|заказчика)\s*[:\n]?\s*([0-5](?:[.,]\d+)?)\s*(?:/\s*5|из\s*5)", text, re.IGNORECASE)
    paid = re.search(r"(?:Выполнено\s+и\s+оплачено\s+заказов|Завершено\s+и\s+оплачено\s+заказов)\s*[:\n]?\s*(\d+)", text, re.IGNORECASE)
    return {
        "rating": float(rating[1].replace(",", ".")) if rating else None,
        "completed_paid_orders": int(paid[1]) if paid else None,
    }


def parse_kwork_card_buyer_stats(text: str) -> dict:
    """Read the listing's project count and hire percentage without inferring payments."""
    text = text.replace("\xa0", " ")
    projects = list(re.finditer(r"Размещено\s+проектов\s+на\s+бирже\s*:\s*([0-9](?:[ \t]*[0-9])*)", text, re.IGNORECASE))
    hired = list(re.finditer(r"Нанято\s*:\s*(\d{1,3})\s*%", text, re.IGNORECASE))
    result = {}
    if len(projects) == 1:
        result["projects_posted"] = int(re.sub(r"\s", "", projects[0][1]))
    if len(hired) == 1 and 0 <= int(hired[0][1]) <= 100:
        result["hire_percent"] = int(hired[0][1])
    return result
