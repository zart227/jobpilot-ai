"""Tests for ops_json_cli helpers."""

from scripts.ops_json_cli import parse_json_stdout


def test_parse_json_stdout_skips_log_prefix():
    raw = "2026-11-02 info log line\n{\n  \"ok\": true\n}\n"
    assert parse_json_stdout(raw) == {"ok": True}
