from app.services.kwork_portfolio_filter import job_requires_kwork_portfolio


def _job(title: str, description: str = "") -> dict:
    return {
        "platform": "kwork",
        "title": title,
        "description": description,
        "skills": [],
    }


def test_rejects_new_site_projects() -> None:
    requires, reason = job_requires_kwork_portfolio(
        _job("Создать сайт", "Нужен лендинг для компании")
    )
    assert requires is True
    assert "портфолио" in reason.lower()


def test_rejects_marketplace_mvp() -> None:
    requires, _ = job_requires_kwork_portfolio(_job("Build Marketplace MVP", "React + Next.js"))
    assert requires is True


def test_allows_telegram_bot() -> None:
    requires, _ = job_requires_kwork_portfolio(
        _job("Разработка Telegram-бота", "aiogram, OpenAI")
    )
    assert requires is False


def test_allows_parser_and_integration() -> None:
    requires, _ = job_requires_kwork_portfolio(
        _job("Парсинг планов-графиков", "Python script for zakupki.gov.ru")
    )
    assert requires is False

    requires, _ = job_requires_kwork_portfolio(
        _job(
            "Python-интеграция 1С, Битрикс",
            "Интеграция для интернет-магазина",
        )
    )
    assert requires is False


def test_allows_site_maintenance() -> None:
    requires, _ = job_requires_kwork_portfolio(
        _job("Доработка сайта на Laravel", "Исправить баг в каталоге")
    )
    assert requires is False
