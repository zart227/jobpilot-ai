from app.services.fl_ru_simple_filter import fl_ru_listing_reject_reason, fl_ru_title_budget_quick_reject


def test_fl_ru_rejects_complex_phrases() -> None:
    reason = fl_ru_listing_reject_reason(
        title="CRM с нуля для отдела продаж",
        description="Нужна команда",
    )
    assert reason is not None


def test_fl_ru_rejects_high_budget() -> None:
    reason = fl_ru_listing_reject_reason(
        title="Доработка API",
        description="Мелкая правка",
        budget_max=150_000,
        max_budget_rub=100_000,
    )
    assert reason is not None


def test_fl_ru_allows_small_task() -> None:
    reason = fl_ru_listing_reject_reason(
        title="Починить webhook в FastAPI",
        description="Один эндпоинт не отвечает",
        budget_max=15_000,
        max_budget_rub=100_000,
    )
    assert reason is None


def test_fl_ru_title_spam() -> None:
    assert fl_ru_title_budget_quick_reject("Написание отзыва", "") is not None
