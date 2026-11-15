from app.utils.delivery_errors import humanize_kwork_browser_error


def test_chrome_error_page_gets_plain_summary() -> None:
    raw = (
        'Page.goto: Navigation to "https://kwork.ru/projects/1/view" is interrupted by '
        'another navigation to "chrome-error://chromewebdata/"'
    )
    text = humanize_kwork_browser_error(raw)
    assert text is not None
    assert "Браузер не смог загрузить" in text
    assert "Повторите отправку" in text


def test_unknown_error_returns_none() -> None:
    assert humanize_kwork_browser_error("Форма отклика недоступна") is None
