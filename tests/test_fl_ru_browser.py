from app.services.fl_ru_browser import is_fl_login_url


def test_is_fl_login_url() -> None:
    assert is_fl_login_url("https://www.fl.ru/account/login/")
    assert not is_fl_login_url("https://www.fl.ru/projects/")
