from app.services.kwork_browser import _kwork_navigation_error_retriable


def test_chrome_error_is_retriable() -> None:
    assert _kwork_navigation_error_retriable(
        'interrupted by another navigation to "chrome-error://chromewebdata/"'
    )


def test_form_validation_not_retriable() -> None:
    assert not _kwork_navigation_error_retriable("Форма отклика недоступна")
