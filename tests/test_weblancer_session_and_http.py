import json
from pathlib import Path

import pytest

from app.services.weblancer_listing_parse import is_cloudflare_challenge_html, parse_listing_html
from app.services.weblancer_session import sanitize_storage_state


def test_sanitize_drops_cf_clearance_on_cloudflare_com() -> None:
    payload = {
        "cookies": [
            {
                "name": "cf_clearance",
                "value": "bad",
                "domain": ".cloudflare.com",
                "path": "/",
                "partitionKey": "https://weblancer.net",
            },
            {
                "name": "cf_clearance",
                "value": "ok",
                "domain": ".weblancer.net",
                "path": "/",
            },
        ],
        "origins": [],
    }
    cleaned = sanitize_storage_state(payload)
    assert len(cleaned["cookies"]) == 1
    assert cleaned["cookies"][0]["domain"] == ".weblancer.net"


def test_sanitize_storage_drops_google_cookies() -> None:
    payload = {
        "cookies": [
            {"name": "SID", "value": "x", "domain": ".google.com", "path": "/"},
            {"name": "token", "value": "y", "domain": ".weblancer.net", "path": "/"},
            {
                "name": "cf_clearance",
                "value": "z",
                "domain": ".weblancer.net",
                "path": "/",
            },
        ],
        "origins": [{"origin": "https://www.google.com", "localStorage": []}],
    }
    cleaned = sanitize_storage_state(payload)
    names = {c["name"] for c in cleaned["cookies"]}
    assert names == {"token", "cf_clearance"}
    assert cleaned["origins"] == []


def test_parse_listing_fixture() -> None:
    html = Path(__file__).parent.joinpath("fixtures/weblancer_listing_snippet.html").read_text()
    rows = parse_listing_html(html)
    assert len(rows) == 1
    assert rows[0].title == "Парсинг сайта"
    assert rows[0].href.endswith("1203726/")


def test_cloudflare_marker_detection() -> None:
    assert is_cloudflare_challenge_html("<title>Один момент…</title>")
    assert not is_cloudflare_challenge_html("<div class='cols_table'>")


@pytest.mark.asyncio
async def test_fetch_http_uses_sanitized_session(monkeypatch) -> None:
    from app.services import weblancer_fetch as mod

    html = Path(__file__).parent.joinpath("fixtures/weblancer_listing_snippet.html").read_text()

    class FakeResponse:
        status_code = 200
        text = html

    class FakeClient:
        cookies = None

        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str):
            return FakeResponse()

    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        mod,
        "storage_state_path",
        lambda: Path("tests/fixtures/weblancer_session_minimal.json"),
    )
    Path("tests/fixtures/weblancer_session_minimal.json").write_text(
        json.dumps(
            sanitize_storage_state(
                {
                    "cookies": [
                        {"name": "token", "value": "t", "domain": ".weblancer.net", "path": "/"},
                    ],
                    "origins": [],
                }
            )
        ),
        encoding="utf-8",
    )
    result = await mod.fetch_html_http("https://www.weblancer.net/test/")
    assert result == html
