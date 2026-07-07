import re

import structlog


def normalize_ru_number(text: str) -> str:
    """Join spaced thousands: '10 000' -> '10000'."""
    cleaned = text.replace("\xa0", " ")
    return re.sub(r"(?<=\d) (?=\d)", "", cleaned)


def competitive_offer_price(
    budget_min: float | None,
    budget_max: float | None,
    discount_percent: float = 15.0,
    min_price: int = 500,
) -> int | None:
    """
    Kwork stores buyer desired budget in budget_min and allowable cap in budget_max.
    Bid below desired to stay competitive (especially without reviews).
    """
    desired = budget_min if budget_min is not None else budget_max
    if not desired:
        return None

    desired_int = int(desired)
    price = int(desired_int * (1 - discount_percent / 100))
    price = (price // 500) * 500
    price = max(min_price, price)

    if price >= desired_int:
        price = max(min_price, ((desired_int - 500) // 500) * 500)

    return price


def format_offer_price(
    budget_min: float | None,
    budget_max: float | None,
    currency: str = "RUB",
    discount_percent: float = 15.0,
) -> str | None:
    price = competitive_offer_price(budget_min, budget_max, discount_percent)
    if price is None:
        return None
    symbol = {"RUB": "₽", "USD": "$", "EUR": "€"}.get(currency, currency)
    return f"{price:,}".replace(",", " ") + f" {symbol}"


def format_budget(
    budget_min: float | None,
    budget_max: float | None,
    currency: str = "USD",
    platform: str | None = None,
) -> str:
    if platform == "kwork" and currency == "RUB":
        if budget_max is not None and budget_max < 50:
            budget_min, budget_max = None, None
        elif budget_min is not None and budget_min < 50 and not budget_max:
            budget_min = None

    symbol = {"RUB": "₽", "USD": "$", "EUR": "€"}.get(currency, currency)

    def fmt(value: float) -> str:
        if value == int(value):
            return f"{int(value):,}".replace(",", " ")
        return f"{value:,.2f}".replace(",", " ")

    if platform == "kwork" and budget_min and budget_max and budget_min != budget_max:
        return (
            f"желаемый до {fmt(budget_min)} {symbol} "
            f"(допустимый до {fmt(budget_max)} {symbol})"
        )
    if budget_min and budget_max and budget_min != budget_max:
        return f"{fmt(budget_min)} – {fmt(budget_max)} {symbol}"
    if budget_max:
        prefix = "до " if platform == "kwork" else ""
        return f"{prefix}{fmt(budget_max)} {symbol}"
    if budget_min:
        return f"от {fmt(budget_min)} {symbol}"
    return "Не указан"


def ensure_proposal_greeting(text: str, language: str = "ru") -> str:
    """Prepend a greeting if the proposal starts without one."""
    stripped = text.lstrip()
    if language.startswith("ru"):
        if not stripped.lower().startswith(("здравствуйте", "добрый", "привет")):
            return f"Здравствуйте. {stripped}"
    elif not stripped.lower().startswith(("hello", "hi", "hey", "dear")):
        return f"Hello. {stripped}"
    return text


def ensure_greeting(text: str, language: str = "ru") -> str:
    text = text.strip()
    if not text:
        return text
    head = text[:40].lower()
    if language == "ru":
        if not any(g in head for g in ("здравствуйте", "добрый день", "добрый вечер")):
            return f"Здравствуйте. {text}"
    elif not any(g in head for g in ("hello", "hi", "dear")):
        return f"Hello. {text}"
    return text


def detect_job_language(title: str, description: str) -> str:
    sample = f"{title} {description[:200]}"
    return "ru" if re.search(r"[а-яА-ЯёЁ]", sample) else "en"


def ensure_proposal_greeting(text: str, *, russian: bool = True) -> str:
    """Prepend a greeting if the proposal does not already start with one."""
    stripped = text.strip()
    if not stripped:
        return stripped
    lower = stripped.lower()
    if lower.startswith(("здравствуйте", "привет", "добрый день", "добрый вечер", "hello", "hi")):
        return stripped
    greeting = "Здравствуйте." if russian else "Hello."
    return f"{greeting} {stripped}"


def sanitize_proposal_text(text: str) -> str:
    """Plain text only: no markdown asterisks or em dashes."""
    text = re.sub(r"\*+([^*]+)\*+", r"\1", text)
    text = text.replace("—", ", ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


KWORK_MIN_OFFER_CHARS = 150
KWORK_MAX_OFFER_CHARS = 2000

KWORK_GENERIC_PHRASES = (
    "имею большой опыт",
    "готов взяться",
    "качественно и в срок",
    "с восьмилетним стажем",
    "более 1000",
    "уникальное предложение",
    "лучшие цены",
)


def kwork_greeting(client_name: str | None) -> str:
    name = (client_name or "").strip()
    if name:
        return f"Добрый день, {name}!"
    return "Добрый день!"


def ensure_kwork_greeting(text: str, client_name: str | None = None) -> str:
    stripped = text.strip()
    if not stripped:
        return kwork_greeting(client_name)

    lower = stripped.lower()
    if any(
        lower.startswith(g)
        for g in ("здравствуйте", "добрый день", "добрый вечер", "привет", "добрый")
    ):
        if client_name and client_name.lower() not in lower[:80]:
            stripped = re.sub(
                r"^(Здравствуйте|Добрый день|Добрый вечер|Привет)[!.]?\s*",
                f"{kwork_greeting(client_name)} ",
                stripped,
                count=1,
                flags=re.IGNORECASE,
            )
        return stripped
    return f"{kwork_greeting(client_name)} {stripped}"


def compose_kwork_submission(
    proposal: str,
    execution_plan: str | None = None,
    timeline: str | None = None,
) -> str:
    """Prepare proposal text for Kwork (only PROPOSAL is sent; plan/timeline are internal)."""
    text = sanitize_proposal_text(proposal)
    if len(text) < KWORK_MIN_OFFER_CHARS:
        text = f"{text}\n\nГотов приступить к задаче в ближайшее время."
    if len(text) > KWORK_MAX_OFFER_CHARS:
        structlog.get_logger(__name__).warning(
            "Kwork submission exceeds platform limit",
            length=len(text),
            max_chars=KWORK_MAX_OFFER_CHARS,
        )
    return text


def finalize_kwork_proposal(
    text: str,
    *,
    client_name: str | None,
    offer_price: int | None,
    currency: str = "RUB",
) -> str:
    if not text:
        return text

    text = ensure_kwork_greeting(text, client_name)

    lower = text.lower()
    for phrase in KWORK_GENERIC_PHRASES:
        if phrase in lower:
            structlog.get_logger(__name__).warning(
                "Kwork proposal contains generic phrase",
                phrase=phrase,
            )

    if offer_price:
        symbol = {"RUB": "₽", "USD": "$", "EUR": "€"}.get(currency, currency)
        formatted = f"{offer_price:,}".replace(",", " ") + f" {symbol}"
        if formatted not in text and str(offer_price) not in text:
            text = f"{text.rstrip()}\n\nСтоимость: {formatted}."

    if "?" not in text:
        text = (
            f"{text.rstrip()}\n\n"
            "Уточню пару моментов по задаче:\n"
            "1. Есть ли дополнительные требования, не указанные в описании?\n"
            "2. Насколько критичны сроки?"
        )

    return compose_kwork_submission(text)


def ensure_proposal_greeting(text: str, language: str = "ru") -> str:
    """Prepend a greeting if the proposal does not start with one."""
    stripped = text.lstrip()
    if not stripped:
        return text
    lower = stripped.lower()
    greetings_ru = ("здравствуйте", "добрый день", "добрый вечер", "привет")
    greetings_en = ("hello", "hi", "dear", "good morning", "good afternoon")
    greetings = greetings_ru if language == "ru" else greetings_en
    if any(lower.startswith(g) for g in greetings):
        return text
    greeting = "Здравствуйте." if language == "ru" else "Hello."
    return f"{greeting} {stripped}"
