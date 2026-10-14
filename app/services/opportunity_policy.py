"""Evidence-based eligibility rules for the first-review strategy."""
import math
import re

REVIEW_WEIGHTS = {
    "skill_match": 0.25, "budget": 0.05, "complexity": 0.35,
    "client_quality": 0.25, "competition": 0.10,
}


def client_rejection(job: dict, minimum_rating: float, minimum_paid: int) -> str | None:
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
