import pytest

from app.llm.ollama_free_usage import (
    parse_free_usage_entitlement,
    parse_free_usage_model_list,
    parse_settings_account_email,
)


def test_extracts_only_labelled_free_usage_list():
    html = '''<ul><li><a>paid-model</a></li></ul>
    <div id="free-plan-models"><div>Free usage credits can be used with the following cloud models:</div>
    <ul><li><a href="/library/gpt-oss">gpt-oss:20b</a></li>
    <li><a href="/library/gemma4">gemma4:31b</a></li></ul></div>
    <ul><li><a>later-paid-model</a></li></ul>'''
    assert parse_free_usage_model_list(html) == ['gpt-oss:20b', 'gemma4:31b']


@pytest.mark.parametrize('html', [
    '<html>Sign in</html>',
    '<div>Free usage credits can be used with the following cloud models:</div><ul></ul>',
    '<div>Free usage credits can be used with the following cloud models:</div><ul><li><a>not a model!</a></li></ul>',
    '<div>Free usage credits can be used with the following cloud models:</div><ul><li><a>same</a></li><li><a>same</a></li></ul>',
])
def test_unrecognized_page_fails_closed(html):
    with pytest.raises(ValueError):
        parse_free_usage_model_list(html)


def test_account_quota_and_retired_models():
    html = '''<span id="extra-usage-balance">$0</span>
    <button id="extra-usage-reload-toggle" aria-checked="false"></button>
    <div id="free-plan-models"><svg><circle></circle><path></path></svg>
    <p>Free usage credits can be used with the following cloud models:</p>
    <ul><li><a>gpt-oss:20b</a></li><li><a>nemotron-3-nano:30b</a></li></ul></div>
    <p>The following models will be retired soon. Please switch models.</p>
    <ul><li>nemotron-3-nano:30b in 1 week (November 9, 2026)</li></ul>
    <span>Free usage</span><div aria-label="Free usage: 0%"></div>'''
    assert parse_free_usage_entitlement(html) == (['gpt-oss:20b'], 0)
    with pytest.raises(ValueError, match='quota'):
        parse_free_usage_entitlement(html.replace('Free usage: 0%', 'Free usage: 100%'))
    with pytest.raises(ValueError, match='Retirement'):
        parse_free_usage_entitlement(html.replace('nemotron-3-nano:30b in 1 week', 'unknown in 1 week'))
    with pytest.raises(ValueError, match='balance or auto-reload'):
        parse_free_usage_entitlement(html.replace('aria-checked="false"', 'aria-checked="true"'))
    with pytest.raises(ValueError, match='balance or auto-reload'):
        parse_free_usage_entitlement(html.replace('>$0</span>', '>$1</span>'))


def test_account_email_only_from_account_menu_and_rejects_ambiguity():
    assert parse_settings_account_email('<details id="settings-account-menu"><span>A@example.test</span><div>a@example.test</div></details>') == 'a@example.test'
    with pytest.raises(ValueError):
        parse_settings_account_email('<details id="settings-account-menu"><span>a@example.test</span><span>b@example.test</span></details>')
