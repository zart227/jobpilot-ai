from app.services.weblancer_fetch import flaresolverr_base_url, session_cookies_for_flaresolverr
from app.services.weblancer_send import (
    WEBLANCER_ORDER_CLOSED_ERROR,
    _cookies_indicate_guest,
    _playwright_cookie,
    is_weblancer_order_closed_error,
)


def test_is_weblancer_order_closed_error() -> None:
    assert is_weblancer_order_closed_error(WEBLANCER_ORDER_CLOSED_ERROR)
    assert not is_weblancer_order_closed_error("Сессия Weblancer недействительна")


def test_cookies_indicate_guest() -> None:
    assert _cookies_indicate_guest([{"name": "stored_login", "value": "guest_17"}])
    assert not _cookies_indicate_guest([{"name": "stored_login", "value": "freelancer42"}])


def test_playwright_cookie_defaults() -> None:
    out = _playwright_cookie({"name": "token", "value": "x", "domain": ".weblancer.net"})
    assert out["domain"] == ".weblancer.net"
    assert out["path"] == "/"


def test_flaresolverr_base_url_docker_default(monkeypatch) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("WEBLANCER_FLARESOLVERR_URL", "")
    monkeypatch.setenv("JOBPILOT_IN_DOCKER", "1")
    get_settings.cache_clear()
    assert flaresolverr_base_url() == "http://flaresolverr:8191"
    get_settings.cache_clear()


def test_session_cookies_for_flaresolverr_filters_domain(tmp_path, monkeypatch) -> None:
    from app.config import get_settings

    path = tmp_path / "weblancer_session.json"
    path.write_text(
        '{"cookies": ['
        '{"name": "token", "value": "a", "domain": ".weblancer.net", "path": "/"},'
        '{"name": "other", "value": "b", "domain": ".example.com", "path": "/"}'
        ']}',
        encoding="utf-8",
    )
    get_settings.cache_clear()
    monkeypatch.setenv("WEBLANCER_STORAGE_STATE", str(path))
    get_settings.cache_clear()
    cookies = session_cookies_for_flaresolverr()
    assert len(cookies) == 1
    assert cookies[0]["name"] == "token"
    get_settings.cache_clear()
